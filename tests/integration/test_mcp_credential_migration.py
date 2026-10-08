"""真实数据库验证旧 MCP 凭据无损转换、其他用途不变及转换失败整体回滚。"""

import base64
import json
from uuid import uuid4

import pytest
import sqlalchemy as sa
from alembic.config import Config
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from alembic import command
from creativity_service.core.config import Settings
from creativity_service.core.primitives import canonical_json

pytestmark = pytest.mark.integration


@pytest.mark.parametrize("invalid_key", [False, True])
def test_mcp_conversion_and_rollback(monkeypatch, invalid_key):
    master = b"m" * 32
    monkeypatch.setenv(
        "CREATIVITY_MCP_ENCRYPTION_KEYS",
        json.dumps({"legacy": base64.b64encode(b"x" * 32 if invalid_key else master).decode()}),
    )
    engine = sa.create_engine(Settings().database_url.get_secret_value())
    schema = f"test_mcp_plain_{uuid4().hex}"
    config = Config("alembic.ini")
    config.set_main_option("version_table_schema", schema)
    rows = []
    for channel, environment, purpose, value in (
        ("channel_a", "dev", "mcp", "app-secret"),
        ("channel_b", "prod", "mcp", '{"access_token":"oauth-token"}'),
        ("channel_a", "prod", "model", "model-secret"),
    ):
        nonce = bytes(range(12))
        identifier = "model-id" if purpose == "model" else "same-id"
        aad = canonical_json([channel, environment, identifier, purpose])
        rows.append(
            {
                "id": identifier,
                "channel_id": channel,
                "environment": environment,
                "purpose": purpose,
                "ciphertext": nonce + AESGCM(master).encrypt(nonce, value.encode(), aad),
                "key_version": "legacy",
                "state": "ACTIVE",
                "revision": 7,
            }
        )
    try:
        with engine.begin() as connection:
            connection.execute(sa.text(f'CREATE SCHEMA "{schema}"'))
            connection.execute(sa.text(f'SET LOCAL search_path TO "{schema}"'))
            config.attributes["connection"] = connection
            command.upgrade(config, "0045_remove_legacy_http")
            connection.execute(
                sa.text(
                    "INSERT INTO credentials "
                    "(id, channel_id, environment, purpose, ciphertext, key_version, "
                    "state, revision) VALUES (:id, :channel_id, :environment, :purpose, "
                    ":ciphertext, :key_version, :state, :revision)"
                ),
                rows,
            )

        def upgrade():
            with engine.begin() as connection:
                connection.execute(sa.text(f'SET LOCAL search_path TO "{schema}"'))
                config.attributes["connection"] = connection
                command.upgrade(config, "0046_mcp_plain_credentials")

        if invalid_key:
            with pytest.raises(RuntimeError, match="旧 MCP 凭据转换失败"):
                upgrade()
        else:
            upgrade()
            monkeypatch.setenv("CREATIVITY_MCP_ENCRYPTION_KEYS", "{}")
            upgrade()
        with engine.connect() as connection:
            connection.execute(sa.text(f'SET search_path TO "{schema}"'))
            actual = (
                connection.execute(
                    sa.text("SELECT * FROM credentials ORDER BY channel_id, environment")
                )
                .mappings()
                .all()
            )
            expected = sorted(rows, key=lambda row: (row["channel_id"], row["environment"]))
            for row, old in zip(actual, expected, strict=True):
                assert row["revision"] == 7 and row["state"] == "ACTIVE"
                if invalid_key or old["purpose"] == "model":
                    assert row["ciphertext"] == old["ciphertext"]
                    assert row["key_version"] == "legacy"
                    assert row.get("secret_value") is None
                else:
                    assert row["secret_value"] in {"app-secret", '{"access_token":"oauth-token"}'}
                    assert row["ciphertext"] is None and row["key_version"] is None
            if invalid_key:
                assert "secret_value" not in {
                    c["name"] for c in sa.inspect(connection).get_columns("credentials")
                }
    finally:
        with engine.begin() as connection:
            connection.execute(sa.text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
        engine.dispose()
