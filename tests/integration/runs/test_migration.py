"""初始基线在独立 schema 内往返迁移并审查实际表、字段注释和普通索引。"""

from uuid import uuid4

import pytest
from alembic.config import Config
from sqlalchemy import create_engine, text

from alembic import command
from creativity_service.core.config import Settings
from creativity_service.core.database.audit import audit_database

pytestmark = pytest.mark.integration


def test_initial_baseline_roundtrip_and_actual_database_audit():
    schema = f"test_runs_migration_{uuid4().hex}"
    engine = create_engine(Settings().database_url.get_secret_value())
    try:
        with engine.begin() as connection:
            connection.execute(text(f'CREATE SCHEMA "{schema}"'))
            connection.execute(text(f'SET LOCAL search_path TO "{schema}"'))
            config = Config("alembic.ini")
            config.set_main_option("version_table_schema", schema)
            config.attributes["connection"] = connection
            command.upgrade(config, "head")
            assert audit_database(connection, schema) == []
            command.downgrade(config, "base")
            command.upgrade(config, "head")
            assert audit_database(connection, schema) == []
    finally:
        with engine.begin() as connection:
            connection.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
        engine.dispose()
