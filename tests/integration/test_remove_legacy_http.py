"""旧业务接入升级清理只影响指定用途，保留共享凭据及身份委托。"""

from uuid import uuid4

import pytest
from alembic.config import Config
from sqlalchemy import create_engine, inspect, text

from alembic import command
from creativity_service.core.config import Settings

pytestmark = pytest.mark.integration


def test_removal_cleans_legacy_records_and_preserves_other_credentials():
    schema = f"test_http_removal_{uuid4().hex}"
    config = Config("alembic.ini")
    config.set_main_option("version_table_schema", schema)
    engine = create_engine(Settings().database_url.get_secret_value())
    try:
        with engine.begin() as connection:
            connection.execute(text(f'CREATE SCHEMA "{schema}"'))
            connection.execute(text(f'SET LOCAL search_path TO "{schema}"'))
            config.attributes["connection"] = connection
            command.upgrade(config, "0044_mcp_client_credentials")
            for channel in ("channel_a", "channel_b"):
                for purpose in ("http_tool", "model", "mcp", "delegation", "webhook"):
                    connection.execute(
                        text(
                            "INSERT INTO credentials "
                            "(id, channel_id, environment, purpose, "
                            "ciphertext, key_version, state) "
                            "VALUES (:id, :channel, 'test', :purpose, "
                            ":ciphertext, :version, 'ACTIVE')"
                        ),
                        {
                            "id": purpose,
                            "channel": channel,
                            "purpose": purpose,
                            "ciphertext": b"preserved-ciphertext",
                            "version": "same-master-version",
                        },
                    )
            # 第二渠道的旧密文没有连接引用，升级也应清理，不能留下孤立秘密。
            connection.execute(
                text(
                    "INSERT INTO integrations (id, channel_id, environment, credential_ref) "
                    "VALUES ('legacy', 'channel_a', 'test', 'http_tool')"
                )
            )
            connection.execute(
                text(
                    "INSERT INTO integration_tests (id, channel_id, integration_id) "
                    "VALUES ('test', 'channel_a', 'legacy')"
                )
            )
            for name in ("delegation_keys", "delegation_nonces", "subject_review_bindings"):
                connection.execute(
                    text(f"INSERT INTO {name} (id, channel_id) VALUES ('retained', 'channel_a')")
                )
            retained = connection.execute(
                text(
                    "SELECT * FROM credentials WHERE purpose <> 'http_tool' ORDER BY channel_id, id"
                )
            ).all()
            command.upgrade(config, "0045_remove_legacy_http")
            command.upgrade(config, "0045_remove_legacy_http")
            tables = inspect(connection).get_table_names(schema=schema)
            assert not {"integrations", "integration_tests"}.intersection(tables)
            assert (
                connection.execute(text("SELECT * FROM credentials ORDER BY channel_id, id")).all()
                == retained
            )
            for name in ("delegation_keys", "delegation_nonces", "subject_review_bindings"):
                assert connection.execute(text(f"SELECT id, channel_id FROM {name}")).one() == (
                    "retained",
                    "channel_a",
                )
    finally:
        with engine.begin() as connection:
            connection.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
        engine.dispose()
