"""核心集成测试使用真实 MySQL 8 独立 schema；替身只在这里装配。"""

from uuid import uuid4

import pytest
from alembic.config import Config
from sqlalchemy import create_engine, make_url, text
from sqlalchemy.ext.asyncio import create_async_engine

from alembic import command
from creativity_service.core.config import Settings
from creativity_service.core.context import AuthContext, Scope
from creativity_service.core.deletion import RecoveryService
from creativity_service.core.primitives import ServiceError


class TestAuthorization:
    denied = False

    async def require(self, context, action, resource_id):
        if self.denied:
            raise ServiceError("FORBIDDEN", "当前授权已撤销", 403)


class TestVersionValidator:
    async def validate(self, uow, context, version, operation):
        uow.require_scope(context.scope)


@pytest.fixture(scope="session")
def database_schema():
    engine = create_engine(Settings().database_url.get_secret_value())
    name = f"test_core_{uuid4().hex}"
    with engine.connect() as connection:
        connection.execute(
            text(f"CREATE DATABASE `{name}` CHARACTER SET utf8mb4 COLLATE utf8mb4_0900_bin")
        )
        connection.execute(text(f"USE `{name}`"))
        config = Config("alembic.ini")
        config.set_main_option("version_table_schema", name)
        config.attributes["connection"] = connection
        command.upgrade(config, "head")
    yield name
    with engine.connect() as connection:
        connection.execute(text(f"DROP DATABASE `{name}`"))
    engine.dispose()


@pytest.fixture
async def engine(database_schema):
    engine = create_async_engine(
        make_url(Settings().async_database_url).set(database=database_schema),
        isolation_level="READ COMMITTED",
        pool_size=10,
        max_overflow=10,
    )
    yield engine
    await engine.dispose()


@pytest.fixture
def authorization():
    return TestAuthorization()


@pytest.fixture
def validator():
    return TestVersionValidator()


@pytest.fixture
async def context(engine, authorization):
    context = AuthContext(
        scope=Scope(channel_id=f"channel_{uuid4().hex}", environment="test"),
        principal_type="management",
        principal_id="test_admin",
        actor_id="test_admin",
        request_id=f"request_{uuid4().hex}",
    )
    await RecoveryService(engine, authorization).initialize_fresh(context)
    return context
