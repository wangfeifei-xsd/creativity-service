"""真实渠道、身份、PostgreSQL 与 Redis 认证夹具，后续模块可复用。"""

from datetime import timedelta
from types import SimpleNamespace
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
from creativity_service.core.config import Settings
from creativity_service.core.primitives import new_id, utcnow
from creativity_service.modules.channels.assembly import build_channel_services
from creativity_service.modules.channels.schemas import (
    ChannelCreate,
    ClientCreate,
    KeyCreate,
    TokenExchange,
)
from creativity_service.modules.iam.schemas import (
    AccountCreate,
    ChannelContextInput,
    LoginInput,
    PasswordChange,
)
from tests.support.captcha import captcha_token

INITIAL = "Initial-password-1234"
PASSWORD = "Changed-password-5678"


async def login(env):
    response = await env.iam.sessions.login(
        LoginInput(
            login_name="root-admin",
            password=PASSWORD,
            captcha_token=await captcha_token(env.iam, "root-admin", "channel-test"),
        ),
        "channel-test",
        new_id("request"),
    )
    return response, await env.iam.authentication.admin_session(
        response.access_token, new_id("request"), governance=True
    )


@pytest.fixture
async def channel_env():
    settings = Settings()
    schema = f"test_chn_{uuid4().hex}"
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
    iam, services = build_channel_services(engine, redis, schema)
    await services.channels.initialize_system()
    app = create_schema_app()
    app.add_middleware(RequestContextMiddleware)
    app.state.iam, app.state.channels, app.state.authentication = iam, services, iam.authentication
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        env = SimpleNamespace(
            iam=iam, services=services, engine=engine, redis=redis, client=client, schema=schema
        )
        account = await iam.accounts.initialize_admin(
            AccountCreate(
                login_name="root-admin", display_name="平台管理员", initial_password=INITIAL
            )
        )
        initial = await iam.sessions.login(
            LoginInput(
                login_name="root-admin",
                password=INITIAL,
                captcha_token=await captcha_token(iam, "root-admin", "channel-test"),
            ),
            "channel-test",
            new_id("request"),
        )
        session = await iam.authentication.admin_session(
            initial.access_token, new_id("request"), allow_initial=True
        )
        await iam.accounts.change_password(
            session, PasswordChange(current_password=INITIAL, new_password=PASSWORD)
        )
        env.admin_token, env.admin = await login(env)
        env.user_id = account.user_id
        yield env
    keys = [key async for key in redis.scan_iter(f"{schema}:*")]
    if keys:
        await redis.delete(*keys)
    await redis.aclose()
    await engine.dispose()
    with sync_engine.begin() as connection:
        connection.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
    sync_engine.dispose()


def channel_body(env, code="rental", scope_type="default"):
    name = (
        "租号渠道"
        if code == "rental" and scope_type == "default"
        else "陪玩渠道"
        if code == "playmate" and scope_type == "club"
        else f"{code.strip()}渠道"
    )
    return ChannelCreate(
        name=name,
        owner="业务负责人",
        first_admin_user_id=env.user_id,
        environment="test",
    )


async def provision(env, code="rental", scope_type="default", *, independent_actions=None):
    body = channel_body(env, code, scope_type)
    if independent_actions is not None:
        body = body.model_copy(update={"independent_actions": independent_actions})
    channel = await env.services.channels.create(env.admin, body)
    _, session = await login(env)
    response = await env.iam.sessions.enter(
        session,
        ChannelContextInput(
            channel_id=channel.channel_id,
            environment="test",
        ),
    )
    manager = await env.iam.authentication.admin_session(
        response.access_token, new_id("request"), governance=True
    )
    return SimpleNamespace(channel=channel, token=response, manager=manager)


async def credential(env, channel, name="业务后端"):
    client = await env.services.channels.create_client(
        channel.manager,
        channel.channel.channel_id,
        ClientCreate(
            name=name,
            environment="test",
            scopes=["run:create", "run:read"],
        ),
    )
    key = await env.services.keys.create(
        channel.manager,
        channel.channel.channel_id,
        KeyCreate(
            name="测试凭据",
            client_id=client.client_id,
            environment="test",
            scopes=client.scopes,
            expires_at=utcnow() + timedelta(minutes=20),
        ),
    )
    response = await env.services.keys.exchange(
        TokenExchange(api_key=key.api_key), new_id("request")
    )
    context = await env.iam.authentication.authenticate(response.access_token, "service")
    return SimpleNamespace(client=client, key=key, token=response, context=context)


@pytest.fixture
async def channel(channel_env):
    return await provision(channel_env)


@pytest.fixture
async def service_identity(channel_env, channel):
    return await credential(channel_env, channel)
