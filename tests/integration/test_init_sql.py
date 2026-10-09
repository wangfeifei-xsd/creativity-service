"""真实 MySQL 8 独立数据库验证归档、数据完整性、重复导入及事务回滚。"""

import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest
from alembic.config import Config
from pymysql.constants import CLIENT
from pymysql.err import ProgrammingError
from sqlalchemy import create_engine, inspect, select

from alembic import command
from creativity_service.core.config import Settings
from creativity_service.core.database.audit import audit_database
from creativity_service.storage import metadata
from scripts.render_order_guidance_seed import load_guidance, merge_guidance
from scripts.render_weather_seed import load_weather, merged_tables, typed_archive_values

pytestmark = pytest.mark.integration
SCHEMA = Path("sql/init.sql").read_text()
DATA = Path("sql/init_data.sql").read_text()


def execute_script(engine, script):
    raw = engine.raw_connection()
    try:
        with raw.cursor() as cursor:
            cursor.execute(script)
            while cursor.nextset():
                pass
        raw.commit()
    except BaseException:
        raw.rollback()
        raise
    finally:
        raw.close()


@pytest.fixture
def isolated_database():
    from sqlalchemy.engine import make_url

    url = make_url(Settings().database_url.get_secret_value())
    name = "test_mysql_archive_" + uuid4().hex
    admin = create_engine(url)
    with admin.connect() as conn:
        assert str(conn.exec_driver_sql("SELECT VERSION()").scalar()).startswith("8.")
        conn.exec_driver_sql(
            f"CREATE DATABASE `{name}` CHARACTER SET utf8mb4 COLLATE utf8mb4_0900_bin"
        )
    engine = create_engine(
        url.set(database=name), connect_args={"client_flag": CLIENT.MULTI_STATEMENTS}
    )
    try:
        yield SimpleNamespace(engine=engine, name=name)
    finally:
        engine.dispose()
        with admin.connect() as conn:
            conn.exec_driver_sql(f"DROP DATABASE `{name}`")
        admin.dispose()


def counts(connection):
    return {
        name: connection.exec_driver_sql(f"SELECT count(*) FROM `{name}`").scalar()
        for name in inspect(connection).get_table_names()
    }


def test_mysql_archive_preserves_configuration_and_indexes_inside_create_table(isolated_database):
    env = isolated_database
    assert "CREATE INDEX" not in SCHEMA and "\tINDEX ix_" in SCHEMA
    execute_script(env.engine, SCHEMA)
    execute_script(env.engine, DATA)
    base = json.loads(Path("sql/init_data.json").read_text())["tables"]
    weather = load_weather(base)["tables"]
    combined = merged_tables(base, weather)
    archived = merge_guidance(weather, load_guidance(combined)["tables"])
    with env.engine.connect() as conn:
        assert audit_database(conn, env.name) == []
        assert conn.exec_driver_sql("SELECT count(*) FROM transaction_lock_slots").scalar() == 12288
        assert (
            conn.exec_driver_sql("SELECT version_num FROM creativity_alembic_version").scalar()
            == "0048_mysql_milvus"
        )
        for name, rows in archived.items():
            table = metadata.tables[name]
            actual = {r["id"]: dict(r) for r in conn.execute(select(table)).mappings()}
            for row in rows:
                assert actual[row["id"]] == typed_archive_values(table, row), (name, row["id"])
        for name in ("runs", "usage_records", "audit_events", "memory_index_tasks"):
            assert conn.exec_driver_sql(f"SELECT count(*) FROM `{name}`").scalar() == 0
        before = counts(conn)
    # 两个独立连接同时重放，只能观察到已经完成的数据，不产生重复记录。
    with ThreadPoolExecutor(max_workers=2) as pool:
        list(pool.map(lambda _: execute_script(env.engine, DATA), range(2)))
    with env.engine.connect() as conn:
        assert counts(conn) == before
        config = Config("alembic.ini")
        config.attributes["connection"] = conn
        command.upgrade(config, "head")
        assert counts(conn) == before


def test_mysql_data_failure_rolls_back_seed_and_version_marker(isolated_database):
    env = isolated_database
    execute_script(env.engine, SCHEMA)
    broken = DATA.replace("INSERT INTO channels", "INSERT INTO missing_channels", 1)
    with pytest.raises(ProgrammingError):
        execute_script(env.engine, broken)
    with env.engine.connect() as conn:
        assert all(count == 0 for count in counts(conn).values())
    execute_script(env.engine, DATA)
    with env.engine.connect() as conn:
        assert conn.exec_driver_sql("SELECT count(*) FROM creativity_alembic_version").scalar() == 1


def test_mysql_empty_migration_matches_archive_and_can_repeat(isolated_database):
    env = isolated_database
    config = Config("alembic.ini")
    with env.engine.connect() as conn:
        config.attributes["connection"] = conn
        command.upgrade(config, "head")
        command.upgrade(config, "head")
        assert audit_database(conn, env.name) == []
        assert conn.exec_driver_sql("SELECT count(*) FROM transaction_lock_slots").scalar() == 12288
        assert set(inspect(conn).get_table_names()) == set(metadata.tables) | {
            "creativity_alembic_version"
        }
        command.downgrade(config, "base")
        assert inspect(conn).get_table_names() == ["creativity_alembic_version"]
        command.upgrade(config, "head")
        assert audit_database(conn, env.name) == []


def test_baseline_upgrade_preserves_memory_and_explicit_or_implicit_policy(isolated_database):
    env = isolated_database
    config = Config("alembic.ini")
    with env.engine.connect() as conn:
        config.attributes["connection"] = conn
        command.upgrade(config, "head")
        memories = metadata.tables["memories"]
        policies = metadata.tables["memory_policies"]
        conn.execute(
            memories.insert().values(
                id="existing",
                channel_id="channel",
                key="preference",
                value="休闲",
                source_mode="ANY",
            )
        )
        conn.execute(
            policies.insert().values(
                id="policy",
                channel_id="configured",
                agent_id=None,
                max_items=25,
                attributes=[{"key": "custom_attribute"}],
                consolidation={"enabled": True},
            )
        )
        conn.commit()
        command.upgrade(config, "head")
        assert conn.scalar(select(memories.c.value)) == "休闲"
        row = conn.execute(select(policies)).mappings().one()
        assert row["max_items"] == 25 and row["attributes"] == [{"key": "custom_attribute"}]
        assert row["consolidation"] == {"enabled": True}
