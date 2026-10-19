"""真实 MySQL 8 独立数据库验证归档、数据完整性、重复导入及事务回滚。"""

import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest
from alembic.config import Config
from alibabacloud_oss_v2.exceptions import ServiceError as OSSServiceError
from pymysql.constants import CLIENT
from pymysql.err import ProgrammingError
from sqlalchemy import create_engine, inspect, select

from alembic import command
from creativity_service.core.config import Settings
from creativity_service.core.context import Scope
from creativity_service.core.database.audit import audit_database
from creativity_service.core.deletion.ledger import DeletionLedger
from creativity_service.core.files import FileService
from creativity_service.core.primitives import digest
from creativity_service.modules.skills.packages import unpack
from creativity_service.modules.skills.schemas import SkillDefinition
from creativity_service.storage import metadata
from scripts import restore_init_objects
from scripts.import_init_archive import execute_script, initialize
from scripts.render_archive import typed_archive_values
from scripts.render_gpt_seed import load_gpt, merge_gpt
from scripts.render_order_guidance_seed import load_guidance, merge_guidance
from scripts.render_sales_seed import load_sales, merge_sales
from tests.support.file_store import FileURLStore

pytestmark = pytest.mark.integration
SCHEMA = Path("sql/init.sql").read_text()
DATA = Path("sql/init_data.sql").read_text()


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


class ArchiveStore(FileURLStore):
    """保留真实 URL 解析及读写顺序，在隔离数据库测试中替代远端 OSS 网络。"""

    def __init__(self):
        self.objects, self.writes = {}, []

    async def get(self, key, max_bytes):
        if key not in self.objects:
            raise OSSServiceError(
                status_code=404,
                code="NoSuchKey",
                request_id="test",
                message="missing",
                ec="",
                timestamp="",
                request_target="",
            )
        assert len(self.objects[key]) <= max_bytes
        return self.objects[key]

    async def put(self, key, data, content_type, *, forbid_overwrite=False, public=False):
        assert forbid_overwrite and key not in self.objects
        self.objects[key] = data
        self.writes.append((key, public))

    def close(self):
        pass


async def test_mysql_one_command_import_restores_skill_urls_and_refuses_existing_database(
    isolated_database, monkeypatch, tmp_path
):
    env, store = isolated_database, ArchiveStore()
    monkeypatch.setattr(restore_init_objects, "OSSObjectStore", lambda _: store)
    ledger = DeletionLedger(tmp_path / "ledger")
    settings = Settings()
    await initialize(settings, env.engine, ledger)
    items = restore_init_objects.archived_objects(Path("sql/init_data.sql"))
    assert len(store.writes) == 3
    assert [public for _, public in store.writes] == [False, True, True]
    channel = items[0]["channel_id"]
    assert (await ledger.operate(channel, required=True))["entries"] == []
    files = FileService(store, None)
    context = SimpleNamespace(scope=Scope(channel_id=channel, environment="dev"))
    with env.engine.connect() as connection:
        table = metadata.tables["resource_versions"]
        versions = {r["id"]: dict(r) for r in connection.execute(select(table)).mappings()}
        for item in items[1:]:
            row = versions[item["id"]]
            definition = SkillDefinition.model_validate(row["content"])
            assert definition.package_url == store.public_url(item["object_key"])
            data = await files.read(context, definition.package_url)
            assert unpack(data, definition).package_hash == definition.package_hash
            assert row["content_digest"] == digest(
                {"content": row["content"], "output_schema": row["output_schema"]}
            )
        before = counts(connection)
    await restore_init_objects.restore_archive(settings, env.engine, items, check_only=True)
    await restore_init_objects.restore_archive(settings, env.engine, items, check_only=False)
    assert len(store.writes) == 3
    with pytest.raises(ValueError, match="不是空库"):
        await initialize(settings, env.engine, ledger)
    with env.engine.connect() as connection:
        assert counts(connection) == before
        assert {r["id"]: dict(r) for r in connection.execute(select(table)).mappings()} == versions


async def test_mysql_import_does_not_override_existing_deletion_intent(isolated_database, tmp_path):
    ledger = DeletionLedger(tmp_path / "ledger")
    channel = restore_init_objects.archived_objects(Path("sql/init_data.sql"))[0]["channel_id"]
    await ledger.operate(channel, initialize=True, blocked=True)
    with pytest.raises(ValueError, match="封锁"):
        await initialize(Settings(), isolated_database.engine, ledger)
    with isolated_database.engine.connect() as connection:
        assert inspect(connection).get_table_names() == []
    assert (await ledger.operate(channel, required=True))["blocked"]


async def test_mysql_skill_relocation_waits_for_verified_objects(isolated_database, monkeypatch):
    env, store = isolated_database, ArchiveStore()
    execute_script(env.engine, SCHEMA)
    execute_script(env.engine, DATA)
    items = restore_init_objects.archived_objects(Path("sql/init_data.sql"))
    monkeypatch.setattr(restore_init_objects, "OSSObjectStore", lambda _: store)
    # 已有对象发生冲突时不得覆盖，也不得把数据库切到尚未验证的新地址。
    store.objects[items[-1]["object_key"]] = b"conflicting content"
    table = metadata.tables["resource_versions"]
    with env.engine.connect() as connection:
        before = [dict(r) for r in connection.execute(select(table)).mappings()]
    with pytest.raises(ValueError, match="内容不同"):
        await restore_init_objects.restore_archive(Settings(), env.engine, items, check_only=False)
    with env.engine.connect() as connection:
        assert [dict(r) for r in connection.execute(select(table)).mappings()] == before


def test_mysql_archive_preserves_configuration_and_indexes_inside_create_table(isolated_database):
    env = isolated_database
    assert "CREATE INDEX" not in SCHEMA and "\tINDEX ix_" in SCHEMA
    execute_script(env.engine, SCHEMA)
    execute_script(env.engine, DATA)
    base = json.loads(Path("sql/init_data.json").read_text())["tables"]
    guidance = load_guidance(base)["tables"]
    gpt = load_gpt(merge_guidance(base, guidance))["tables"]
    sales = load_sales(merge_gpt(merge_guidance(base, guidance), gpt))["tables"]
    archived = merge_sales(merge_gpt(guidance, gpt), sales)
    with env.engine.connect() as conn:
        assert audit_database(conn, env.name) == []
        assert conn.exec_driver_sql("SELECT count(*) FROM transaction_lock_slots").scalar() == 12288
        assert (
            conn.exec_driver_sql("SELECT version_num FROM creativity_alembic_version").scalar()
            == "0052_file_urls"
        )
        for name, rows in archived.items():
            table = metadata.tables[name]
            actual = {r["id"]: dict(r) for r in conn.execute(select(table)).mappings()}
            for row in rows:
                assert actual[row["id"]] == typed_archive_values(table, row), (name, row["id"])
        before = counts(conn)
        configuration_tables = (
            set(base) | set(archived) | {"transaction_lock_slots", "creativity_alembic_version"}
        )
        assert all(count == 0 for name, count in before.items() if name not in configuration_tables)
        assert before["models"] == before["model_connections"] == before["mcp_connections"] == 2
        assert before["agents"] == 2
        assert before["skills"] == before["prompts"] == 3
        assert before["artifacts"] == 1
        assert before["tools"] == before["mcp_imports"] == 8
        assert before["prompt_samples"] == 0
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


def test_soft_delete_upgrade_backfills_legacy_rows_and_preserves_existing_markers(
    isolated_database,
):
    env = isolated_database
    config = Config("alembic.ini")
    with env.engine.connect() as conn:
        config.attributes["connection"] = conn
        command.upgrade(config, "0048_mysql_milvus")
        conn.exec_driver_sql(
            "INSERT INTO prompts (id, channel_id, status) "
            "VALUES ('gone', 'a', 'DELETED'), ('disabled', 'a', 'DISABLED'), "
            "('existing', 'a', 'ACTIVE')"
        )
        conn.exec_driver_sql(
            "INSERT INTO conversations (id, channel_id, status) "
            "VALUES ('pending', 'a', 'DELETING'), ('archived', 'a', 'ARCHIVED')"
        )
        conn.exec_driver_sql(
            "INSERT INTO memories (id, channel_id, environment, subject_id, subject_type, status) "
            "VALUES ('forgotten', 'a', 'dev', 's', 'user', 'REVOKED'), "
            "('revoked', 'a', 'dev', 's', 'user', 'REVOKED'), "
            "('forgotten', 'b', 'dev', 's', 'user', 'ACTIVE')"
        )
        conn.exec_driver_sql(
            "INSERT INTO deletion_markers "
            "(id, channel_id, environment, subject_id, subject_type, target_type, target_id) "
            "VALUES ('marker', 'a', 'dev', 's', 'user', 'memory', 'forgotten')"
        )
        conn.exec_driver_sql(
            "INSERT INTO channel_memberships (id, channel_id, user_id, status, updated_at) "
            "VALUES ('removed', 'a', 'removed-user', 'DISABLED', '2026-10-01'), "
            "('disabled', 'a', 'disabled-user', 'DISABLED', '2026-10-01'), "
            "('readded', 'a', 'readded-user', 'DISABLED', '2026-10-03')"
        )
        conn.exec_driver_sql(
            "INSERT INTO resource_grants (id, channel_id, allowed_actions, updated_at) "
            "VALUES ('revoked', 'a', '[]', '2026-10-01'), "
            "('empty', 'a', '[]', '2026-10-01')"
        )
        conn.exec_driver_sql(
            "INSERT INTO audit_events "
            "(id, channel_id, target_type, target_id, action, outcome, created_at) "
            "VALUES ('1', 'a', 'account', 'removed-user', 'membership:remove', "
            "'SUCCEEDED', '2026-10-02'), "
            "('2', 'a', 'account', 'readded-user', 'membership:remove', "
            "'SUCCEEDED', '2026-10-02'), "
            "('3', 'a', 'resource_grant', 'revoked', 'grant:revoke', "
            "'SUCCEEDED', '2026-10-02')"
        )
        # 模拟已有库的版本表和 DDL 执行中断，不依赖新模型替代冻结迁移。
        conn.exec_driver_sql("ALTER TABLE creativity_alembic_version DROP COLUMN is_deleted")
        conn.exec_driver_sql(
            "ALTER TABLE prompts ADD COLUMN is_deleted BOOL COMMENT '是否已逻辑删除'"
        )
        conn.exec_driver_sql("UPDATE prompts SET is_deleted=true WHERE id='existing'")
        conn.commit()
        command.upgrade(config, "head")
        command.upgrade(config, "head")
        assert dict(conn.exec_driver_sql("SELECT id, is_deleted FROM prompts").all()) == {
            "gone": 1,
            "disabled": 0,
            "existing": 1,
        }
        assert dict(conn.exec_driver_sql("SELECT id, is_deleted FROM conversations").all()) == {
            "pending": 1,
            "archived": 0,
        }
        assert set(conn.exec_driver_sql("SELECT channel_id, id, is_deleted FROM memories")) == {
            ("a", "forgotten", 1),
            ("a", "revoked", 0),
            ("b", "forgotten", 0),
        }
        assert dict(
            conn.exec_driver_sql("SELECT id, is_deleted FROM channel_memberships").all()
        ) == {
            "removed": 1,
            "disabled": 0,
            "readded": 0,
        }
        assert dict(conn.exec_driver_sql("SELECT id, is_deleted FROM resource_grants").all()) == {
            "revoked": 1,
            "empty": 0,
        }
        assert audit_database(conn, env.name) == []
        with pytest.raises(RuntimeError, match="存在逻辑删除记录"):
            command.downgrade(config, "0048_mysql_milvus")
