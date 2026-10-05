"""在独立临时 schema 验证迁移版本存储，结束后清理测试数据。"""

import io
from pathlib import Path
from shutil import copyfile
from uuid import uuid4

import pytest
from alembic.config import Config
from sqlalchemy import create_engine, text

from alembic import command
from creativity_service.core.config import Settings

pytestmark = pytest.mark.integration


def test_version_storage_has_channel_comments_and_no_unique_index(tmp_path):
    schema = f"test_migration_{uuid4().hex}"
    scripts = tmp_path / "alembic"
    versions = scripts / "versions"
    versions.mkdir(parents=True)
    copyfile("alembic/env.py", scripts / "env.py")
    (versions / "baseline.py").write_text(
        '"""测试用迁移，只验证迁移版本存储。"""\n'
        'revision = "bootstrap_test"\ndown_revision = None\n'
        "def upgrade():\n    pass\ndef downgrade():\n    pass\n",
        encoding="utf-8",
    )
    config = Config(str(Path("alembic.ini").resolve()))
    config.set_main_option("script_location", str(scripts))
    config.set_main_option("version_table_schema", schema)
    engine = create_engine(Settings().database_url.get_secret_value())
    try:
        with engine.begin() as connection:
            connection.execute(text(f'CREATE SCHEMA "{schema}"'))
        command.upgrade(config, "head")
        command.upgrade(config, "head")
        with engine.connect() as connection:
            rows = connection.execute(
                text(f'SELECT version_num, channel_id FROM "{schema}".creativity_alembic_version')
            ).all()
            assert rows == [("bootstrap_test", "system")]
            indexes = connection.execute(
                text("SELECT indexname FROM pg_indexes WHERE schemaname = :schema"),
                {"schema": schema},
            ).all()
            assert indexes == []
            table_comment = connection.execute(
                text("SELECT obj_description(to_regclass(:table))"),
                {"table": f"{schema}.creativity_alembic_version"},
            ).scalar_one()
            assert table_comment == "平台数据库迁移版本记录"
            comments = (
                connection.execute(
                    text(
                        "SELECT col_description(to_regclass(:table), n) "
                        "FROM generate_series(1, 2) n"
                    ),
                    {"table": f"{schema}.creativity_alembic_version"},
                )
                .scalars()
                .all()
            )
            assert comments == ["当前数据库迁移修订编号", "迁移记录所属系统渠道"]
        offline = io.StringIO()
        config.output_buffer = offline
        command.upgrade(config, "head", sql=True)
        assert "'system'" in offline.getvalue()
        assert "PRIMARY KEY" not in offline.getvalue()
        command.downgrade(config, "base")
        with engine.connect() as connection:
            assert (
                connection.execute(
                    text(f'SELECT count(*) FROM "{schema}".creativity_alembic_version')
                ).scalar_one()
                == 0
            )
    finally:
        with engine.begin() as connection:
            connection.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
        engine.dispose()


def test_baseline_upgrade_preserves_existing_subscription_scope():
    schema = f"test_subscription_migration_{uuid4().hex}"
    config = Config("alembic.ini")
    config.set_main_option("version_table_schema", schema)
    engine = create_engine(Settings().database_url.get_secret_value())
    try:
        with engine.begin() as connection:
            connection.execute(text(f'CREATE SCHEMA "{schema}"'))
            connection.execute(text(f'SET LOCAL search_path TO "{schema}"'))
            config.attributes["connection"] = connection
            command.upgrade(config, "head")
            for name in ("webhook_endpoints", "alert_rules"):
                # 已有配置的显式订阅范围不能因基线合并而再次回填或清空。
                connection.execute(
                    text(
                        f"INSERT INTO {name} (id, channel_id, client_ids) "
                        "VALUES ('legacy', 'test', '[\"service-original\"]'::jsonb)"
                    )
                )
            command.upgrade(config, "head")
            for name in ("webhook_endpoints", "alert_rules"):
                assert connection.execute(text(f"SELECT client_ids FROM {name}")).scalar_one() == [
                    "service-original"
                ]
            command.upgrade(config, "head")
            assert connection.execute(
                text("SELECT client_ids FROM webhook_endpoints")
            ).scalar_one() == ["service-original"]
    finally:
        with engine.begin() as connection:
            connection.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
        engine.dispose()


def test_baseline_upgrade_preserves_memory_and_explicit_or_implicit_policy():
    schema = f"test_memory_migration_{uuid4().hex}"
    config = Config("alembic.ini")
    config.set_main_option("version_table_schema", schema)
    engine = create_engine(Settings().database_url.get_secret_value())
    try:
        with engine.begin() as connection:
            connection.execute(text(f'CREATE SCHEMA "{schema}"'))
            connection.execute(text(f'SET LOCAL search_path TO "{schema}"'))
            config.attributes["connection"] = connection
            command.upgrade(config, "head")
            connection.execute(
                text(
                    "INSERT INTO memories (id, channel_id, key, value, source_mode) "
                    "VALUES ('legacy', 'implicit', 'play_style', '\"休闲\"'::jsonb, 'ANY')"
                )
            )
            connection.execute(
                text(
                    "INSERT INTO memory_policies "
                    "(id, channel_id, agent_id, max_items, attributes, consolidation) "
                    "VALUES ('policy', 'configured', NULL, 25, "
                    '\'[{"key":"custom_attribute"}]\'::jsonb, \'{"enabled": true}\'::jsonb)'
                )
            )
            command.upgrade(config, "head")
            assert connection.execute(text("SELECT source_mode, value FROM memories")).one() == (
                "ANY",
                "休闲",
            )
            policies = connection.execute(
                text(
                    "SELECT channel_id, max_items, attributes, consolidation FROM memory_policies "
                    "ORDER BY channel_id"
                )
            ).all()
            assert len(policies) == 1
            assert [(p.channel_id, p.max_items) for p in policies] == [
                ("configured", 25),
            ]
            assert policies[0].attributes == [{"key": "custom_attribute"}]
            assert policies[0].consolidation == {"enabled": True}
            assert (
                connection.execute(text("SELECT count(*) FROM memory_consolidations")).scalar_one()
                == 0
            )
            command.upgrade(config, "head")
            assert connection.execute(text("SELECT value FROM memories")).scalar_one() == "休闲"
            command.upgrade(config, "head")
            assert (
                connection.execute(
                    text(
                        "SELECT count(*) FROM memory_policies WHERE channel_id = 'implicit' "
                        "AND agent_id IS NULL"
                    )
                ).scalar_one()
                == 0
            )
    finally:
        with engine.begin() as connection:
            connection.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
        engine.dispose()
