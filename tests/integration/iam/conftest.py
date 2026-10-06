"""IAM 使用真实 PostgreSQL/Redis；渠道、资源与委托读取器仅为交接夹具。"""

from datetime import timedelta
from uuid import uuid4

import httpx
import pytest
from alembic.config import Config
from redis.asyncio import Redis
from sqlalchemy import create_engine, text
from sqlalchemy.ext.asyncio import create_async_engine

from alembic import command
from creativity_service.api.middleware import RequestContextMiddleware
from creativity_service.app import create_schema_app
from creativity_service.core.auth.types import (
    ResourceState,
    ServiceIdentity,
    SubjectAuthority,
    WorkspaceOption,
)
from creativity_service.core.config import Settings
from creativity_service.core.context import ChannelState, Scope
from creativity_service.core.database import transaction
from creativity_service.core.primitives import new_id, utcnow
from creativity_service.modules.iam.schemas import (
    AccountCreate,
    ChannelContextInput,
    LoginInput,
    PasswordChange,
)
from creativity_service.modules.iam.services import build_iam_services
from tests.support.captcha import captcha_token

INITIAL = "Initial-password-1234"
PASSWORD = "Changed-password-5678"


class ChannelFixture:
    def __init__(self):
        self.options = [
            WorkspaceOption(
                channel_id=c,
                channel_name=name,
                environment=env,
                environment_name="测试" if env == "test" else "生产",
            )
            for c, name in (("channel_a", "渠道甲"), ("channel_b", "渠道乙"))
            for env in ("test", "prod")
        ]
        self.active = True
        self.key_active = True
        self.service_actions = frozenset({"run:read", "run:create"})
        self.subject_actions = self.service_actions
        self.key_expires = utcnow() + timedelta(minutes=5)

    async def list_for(self, user_id):
        return self.options

    async def for_member(self, member):
        return [
            option
            for option in self.options
            if option.channel_id == member.channel_id and option.environment in member.environments
        ]

    async def read_current(self, context):
        return ChannelState(
            channel_id=context.scope.channel_id,
            environment=context.scope.environment,
            channel_active=self.active,
            environment_active=True,
            membership_active=True,
            client_active=True,
            key_active=self.key_active,
        )


class ResourceFixture:
    async def read_current(self, context, resource_type, resource_id):
        if resource_id == "missing":
            return None
        scope = context.scope
        if resource_id == "foreign":
            scope = scope.model_copy(update={"channel_id": "other_channel"})
        return ResourceState(
            scope=scope,
            resource_type=resource_type,
            resource_id=resource_id,
            name="已授权资源",
            active=resource_id != "disabled",
        )


class ServiceFixture:
    def __init__(self, channels):
        self.channels = channels

    async def read_current(self, context):
        return ServiceIdentity(
            channel_id=context.scope.channel_id,
            environment=context.scope.environment,
            client_id="client_a",
            key_id="key_a",
            expires_at=self.channels.key_expires,
            client_actions=self.channels.service_actions,
            key_actions=self.channels.service_actions,
        )


class SubjectFixture:
    def __init__(self, channels):
        self.channels = channels

    async def read_current(self, context):
        return SubjectAuthority(
            scope=context.scope,
            expires_at=utcnow() + timedelta(minutes=1),
            actions=self.channels.subject_actions,
            agent_actions=self.channels.service_actions,
            resources={"run": frozenset({"run_a"})},
        )


@pytest.fixture
async def iam_env():
    settings = Settings()
    schema = f"test_iam_{uuid4().hex}"
    sync_engine = create_engine(settings.database_url.get_secret_value())
    with sync_engine.begin() as connection:
        connection.execute(text(f'CREATE SCHEMA "{schema}"'))
        connection.execute(text(f'SET LOCAL search_path TO "{schema}"'))
        config = Config("alembic.ini")
        config.set_main_option("version_table_schema", schema)
        config.attributes["connection"] = connection
        command.upgrade(config, "head")
    engine = create_async_engine(
        settings.database_url.get_secret_value(),
        connect_args={"options": f"-csearch_path={schema}"},
        pool_size=10,
    )
    redis = Redis.from_url(settings.redis_auth_url.get_secret_value(), socket_timeout=2)
    channels = ChannelFixture()
    iam = build_iam_services(
        engine,
        redis,
        schema,
        channels=channels,
        directory=channels,
        resources=ResourceFixture(),
        services=ServiceFixture(channels),
        subjects=SubjectFixture(channels),
    )
    app = create_schema_app()
    app.add_middleware(RequestContextMiddleware)
    app.state.iam = iam
    app.state.authentication = iam.authentication
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        yield iam, client, channels, engine, redis
    keys = [key async for key in redis.scan_iter(f"{schema}:*")]
    if keys:
        await redis.delete(*keys)
    await redis.aclose()
    await engine.dispose()
    with sync_engine.begin() as connection:
        connection.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
    sync_engine.dispose()


async def login(iam, name, password=PASSWORD, *, initial=False):
    response = await iam.sessions.login(
        LoginInput(
            login_name=name,
            password=password,
            captcha_token=await captcha_token(iam, name, "test-ip"),
        ),
        "test-ip",
        new_id("request"),
    )
    session = await iam.authentication.admin_session(
        response.access_token, new_id("request"), allow_initial=initial
    )
    return response, session


@pytest.fixture
async def admin(iam_env):
    iam = iam_env[0]
    account = await iam.accounts.initialize_admin(
        AccountCreate(login_name="root-admin", display_name="初始管理员", initial_password=INITIAL)
    )
    _, initial = await login(iam, account.login_name, INITIAL, initial=True)
    await iam.accounts.change_password(
        initial, PasswordChange(current_password=INITIAL, new_password=PASSWORD)
    )
    response, session = await login(iam, account.login_name)
    return response, session


async def create_user(iam, admin, name="builder-user"):
    account = await iam.accounts.create(
        admin, AccountCreate(login_name=name, display_name="开发用户", initial_password=INITIAL)
    )
    _, session = await login(iam, name, INITIAL, initial=True)
    await iam.accounts.change_password(
        session, PasswordChange(current_password=INITIAL, new_password=PASSWORD)
    )
    return account, await login(iam, name)


async def provision(iam, admin, channel_id, user_id, independent_actions=None):
    scope = Scope(channel_id=channel_id, environment="test")
    async with transaction(
        iam.accounts.repository.engine, scope, iam.access.provisioning_keys(channel_id, user_id)
    ) as uow:
        await iam.access.provision_first_member(
            uow, admin, user_id, ["test", "prod"], independent_actions
        )


async def enter(iam, session, channel_id="channel_a", environment="test"):
    response = await iam.sessions.enter(
        session,
        ChannelContextInput(channel_id=channel_id, environment=environment),
    )
    return response, await iam.authentication.admin_session(
        response.access_token, new_id("request")
    )


@pytest.fixture
async def manager(iam_env, admin):
    iam = iam_env[0]
    await provision(iam, admin[1], "channel_a", admin[1].account.id)
    _, session = await login(iam, "root-admin")
    return await enter(iam, session)
