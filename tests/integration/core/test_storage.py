"""实际 schema、重复升级、回退及凭据渠道隔离验证。"""

import io
from uuid import uuid4

import pytest
from alembic.config import Config
from pydantic import SecretBytes
from sqlalchemy import cast, create_engine, inspect, literal, select, text

from alembic import command
from creativity_service.core.config import Settings
from creativity_service.core.context import Scope
from creativity_service.core.database import Repository
from creativity_service.core.database.audit import (
    audit_database,
    check_sql,
)
from creativity_service.core.database.tables import metadata
from creativity_service.core.database.types import DocumentJSON
from creativity_service.core.primitives import ServiceError
from creativity_service.core.security.credentials import CredentialService

pytestmark = pytest.mark.integration


async def test_json_object_keys_and_structural_membership(engine):
    document = cast(literal('{"a.b": null, "引号\\"": 1, "items": ["run:read"]}'), DocumentJSON)
    async with engine.connect() as connection:
        result = (
            await connection.execute(
                select(
                    document.has_key("a.b"),
                    document.has_key('引号"'),
                    document.has_key("a"),
                    document.contains({"items": ["run:read"]}),
                    document.contains({"items": ["run"]}),
                )
            )
        ).one()
    assert tuple(result) == (True, True, False, True, False)


async def test_actual_schema_and_framework_storage(engine, database_schema):
    async with engine.connect() as connection:
        failures = await connection.run_sync(lambda conn: audit_database(conn, database_schema))
    assert failures == []


def test_full_migration_repeat_offline_and_downgrade():
    engine = create_engine(Settings().database_url.get_secret_value())
    name = f"test_roundtrip_{uuid4().hex}"
    config = Config("alembic.ini")
    config.set_main_option("version_table_schema", name)
    try:
        with engine.connect() as connection:
            connection.execute(
                text(f"CREATE DATABASE `{name}` CHARACTER SET utf8mb4 COLLATE utf8mb4_0900_bin")
            )
            connection.execute(text(f"USE `{name}`"))
            config.attributes["connection"] = connection
            command.upgrade(config, "head")
            command.upgrade(config, "head")
            assert audit_database(connection, name) == []
            command.downgrade(config, "base")
            assert inspect(connection).get_table_names(schema=name) == [
                "creativity_alembic_version"
            ]
        config.attributes.pop("connection")
        output = io.StringIO()
        config.output_buffer = output
        command.upgrade(config, "head", sql=True)
        assert check_sql(output.getvalue()) == []
        assert "0048_mysql_milvus" in output.getvalue()
    finally:
        with engine.connect() as connection:
            connection.execute(text(f"DROP DATABASE IF EXISTS `{name}`"))
        engine.dispose()


async def test_credential_aad_no_plaintext_and_current_authorization(
    engine, context, authorization
):
    class Keys:
        async def current(self):
            return "key_v1", SecretBytes(b"k" * 32)

        async def resolve(self, version):
            assert version == "key_v1"
            return SecretBytes(b"k" * 32)

    service = CredentialService(engine, Keys(), authorization)
    credential_id = await service.store(context, "model", SecretBytes(b"sensitive-provider-key"))
    async with engine.connect() as connection:
        row = await Repository(metadata.tables["credentials"], context.scope).get(
            connection, credential_id
        )
    assert b"sensitive-provider-key" not in bytes(row["ciphertext"])
    assert row["secret_value"] is None

    async def operation(secret):
        assert secret.get_secret_value() == b"sensitive-provider-key"
        return "已调用"

    assert await service.call(context, credential_id, "model", operation) == "已调用"
    foreign = context.model_copy(update={"scope": Scope(channel_id="foreign", environment="test")})
    with pytest.raises(ServiceError):
        await service.call(foreign, credential_id, "model", operation)
    authorization.denied = True
    with pytest.raises(ServiceError):
        await service.call(context, credential_id, "model", operation)


@pytest.mark.parametrize(
    "value", ["app-secret-for-test", "static-token-for-test", '{"access_token":"oauth-token"}']
)
async def test_mcp_credentials_without_keys_keep_scope_and_authorization(
    engine, context, authorization, value
):
    service = CredentialService(engine, authorization=authorization)
    credential_id = await service.store(context, "mcp", SecretBytes(value.encode()))
    async with engine.connect() as connection:
        row = await Repository(metadata.tables["credentials"], context.scope).get(
            connection, credential_id
        )
    assert row["secret_value"] == value
    assert row["ciphertext"] is None and row["key_version"] is None

    async def operation(secret):
        assert secret.get_secret_value() == value.encode()
        return "已调用"

    assert await service.call(context, credential_id, "mcp", operation) == "已调用"
    for update in ({"channel_id": "foreign"}, {"environment": "prod"}):
        foreign = context.model_copy(update={"scope": context.scope.model_copy(update=update)})
        with pytest.raises(ServiceError):
            await service.call(foreign, credential_id, "mcp", operation)
    authorization.denied = True
    with pytest.raises(ServiceError):
        await service.call(context, credential_id, "mcp", operation)
