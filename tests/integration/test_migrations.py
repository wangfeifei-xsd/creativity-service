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


def test_run_subscription_upgrade_preserves_existing_scope_and_is_reversible():
    schema = f"test_subscription_migration_{uuid4().hex}"
    config = Config("alembic.ini")
    config.set_main_option("version_table_schema", schema)
    engine = create_engine(Settings().database_url.get_secret_value())
    try:
        with engine.begin() as connection:
            connection.execute(text(f'CREATE SCHEMA "{schema}"'))
            connection.execute(text(f'SET LOCAL search_path TO "{schema}"'))
            config.attributes["connection"] = connection
            command.upgrade(config, "0031_identity_operations")
            for name in ("webhook_endpoints", "alert_rules"):
                # 只验证旧存储记录的范围回填，不通过业务入口创建此迁移夹具。
                connection.execute(
                    text(f"INSERT INTO {name} (id, channel_id) VALUES ('legacy', 'test')")
                )
            command.upgrade(config, "head")
            for name in ("webhook_endpoints", "alert_rules"):
                assert connection.execute(text(f"SELECT client_ids FROM {name}")).scalar_one() == []
            command.downgrade(config, "0031_identity_operations")
            command.upgrade(config, "head")
            assert (
                connection.execute(text("SELECT client_ids FROM webhook_endpoints")).scalar_one()
                == []
            )
    finally:
        with engine.begin() as connection:
            connection.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
        engine.dispose()
