"""实际 schema、重复升级、回退及凭据渠道隔离验证。"""

import io
from uuid import uuid4

import pytest
from alembic.config import Config
from pydantic import SecretBytes
from sqlalchemy import create_engine, inspect, text

from alembic import command
from creativity_service.core.config import Settings
from creativity_service.core.context import Scope
from creativity_service.core.database import Repository
from creativity_service.core.database.audit import (
    audit_database,
    check_sql,
)
from creativity_service.core.database.tables import metadata
from creativity_service.core.primitives import ServiceError
from creativity_service.core.security.credentials import CredentialService

pytestmark = pytest.mark.integration


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
        with engine.begin() as connection:
            connection.execute(text(f'CREATE SCHEMA "{name}"'))
            connection.execute(text(f'SET LOCAL search_path TO "{name}"'))
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
        command.upgrade(config, "0040_model_networks:head", sql=True)
        assert check_sql(output.getvalue()) == []
        assert "0041_channel_environments" in output.getvalue()
    finally:
        with engine.begin() as connection:
            connection.execute(text(f'DROP SCHEMA IF EXISTS "{name}" CASCADE'))
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
