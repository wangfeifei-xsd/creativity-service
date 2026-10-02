"""提示词使用独立 PostgreSQL schema，模型与证据替身只存在于测试。"""

from uuid import uuid4

import pytest
from alembic.config import Config
from sqlalchemy import create_engine, text
from sqlalchemy.ext.asyncio import create_async_engine

from alembic import command
from creativity_service.core.config import Settings
from creativity_service.core.context import AuthContext, Scope
from creativity_service.core.deletion import RecoveryService
from creativity_service.core.primitives import ServiceError
from creativity_service.modules.prompts.services import PromptService


class Authorization:
    denied = set()

    async def require(self, context, action, resource_id):
        if action in self.denied:
            raise ServiceError("FORBIDDEN", "无权执行此操作", 403)


@pytest.fixture(scope="session")
def prompt_database_schema():
    engine = create_engine(Settings().database_url.get_secret_value())
    name = f"test_prompts_{uuid4().hex}"
    with engine.begin() as connection:
        connection.execute(text(f'CREATE SCHEMA "{name}"'))
        connection.execute(text(f'SET LOCAL search_path TO "{name}"'))
        config = Config("alembic.ini")
        config.set_main_option("version_table_schema", name)
        config.attributes["connection"] = connection
        command.upgrade(config, "0009_prompts")
    yield name
    with engine.begin() as connection:
        connection.execute(text(f'DROP SCHEMA "{name}" CASCADE'))
    engine.dispose()


@pytest.fixture
async def engine(prompt_database_schema):
    engine = create_async_engine(
        Settings().database_url.get_secret_value(),
        connect_args={"options": f"-csearch_path={prompt_database_schema}"},
    )
    yield engine
    await engine.dispose()


@pytest.fixture
def authorization():
    return Authorization()


@pytest.fixture
async def context(engine, authorization):
    context = AuthContext(
        scope=Scope(
            channel_id=f"channel_{uuid4().hex}", environment="test", data_scope_id="orders"
        ),
        principal_type="management",
        principal_id="admin",
        actor_id="admin",
        request_id=f"req_{uuid4().hex}",
    )
    await RecoveryService(engine, authorization).initialize_fresh(context)
    return context


@pytest.fixture
def service(engine, authorization):
    return PromptService(engine, authorization)
