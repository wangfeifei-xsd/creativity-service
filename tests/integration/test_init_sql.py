"""在真实 PostgreSQL 隔离 schema 验证全量 SQL、迁移衔接及失败回滚。"""

from pathlib import Path
from shutil import copytree, ignore_patterns
from types import SimpleNamespace
from uuid import uuid4

import pytest
from alembic.config import Config
from alembic.script import ScriptDirectory
from redis.asyncio import Redis
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.exc import ProgrammingError
from sqlalchemy.ext.asyncio import create_async_engine

from alembic import command
from creativity_service.core.config import Settings
from creativity_service.core.database.audit import audit_database
from creativity_service.core.primitives import new_id
from creativity_service.modules.channels.assembly import build_channel_services
from creativity_service.modules.channels.initialization import system_channel_values
from creativity_service.modules.iam.roles import ROLE_NAMES
from creativity_service.modules.iam.schemas import AccountCreate, LoginInput
from creativity_service.storage import metadata
from tests.support.captcha import captcha_token

pytestmark = pytest.mark.integration
ARCHIVE = Path(__file__).resolve().parents[2] / "sql/init.sql"


@pytest.fixture
def isolated_database():
    engine = create_engine(Settings().database_url.get_secret_value())
    schema = f"test_init_sql_{uuid4().hex}"
    try:
        # 让归档中的 BEGIN/COMMIT 自行控制事务，验证交付文件的真实执行行为。
        with engine.connect().execution_options(isolation_level="AUTOCOMMIT") as connection:
            connection.exec_driver_sql(f'CREATE SCHEMA "{schema}"')
            try:
                connection.exec_driver_sql(f'SET search_path TO "{schema}"')
                yield SimpleNamespace(engine=engine, connection=connection, schema=schema)
            finally:
                connection.exec_driver_sql("ROLLBACK")
                connection.exec_driver_sql("SET search_path TO public")
                connection.exec_driver_sql(f'DROP SCHEMA "{schema}" CASCADE')
    finally:
        engine.dispose()


def snapshot(connection, schema):
    inspector = inspect(connection)
    return {
        name: {
            "comment": inspector.get_table_comment(name, schema=schema),
            "columns": [
                {**column, "type": str(column["type"])}
                for column in inspector.get_columns(name, schema=schema)
            ],
            "indexes": sorted(
                inspector.get_indexes(name, schema=schema), key=lambda item: item["name"]
            ),
        }
        for name in inspector.get_table_names(schema=schema)
    }


def test_init_sql_matches_migrations_and_supports_followup_upgrade(isolated_database, tmp_path):
    database = isolated_database
    connection = database.connection
    connection.exec_driver_sql(ARCHIVE.read_text(encoding="utf-8"))
    assert audit_database(connection, database.schema) == []
    actual = snapshot(connection, database.schema)
    assert set(actual) == {*metadata.tables, "creativity_alembic_version"}
    config = Config("alembic.ini")
    head = ScriptDirectory.from_config(config).get_current_head()
    assert connection.execute(text("SELECT * FROM creativity_alembic_version")).all() == [
        (head, "system")
    ]
    system = connection.execute(text("SELECT * FROM channels")).mappings().one()
    assert system["id"] == system["channel_id"] == "system"
    assert system["revision"] == 1
    assert system["created_at"] == system["updated_at"]
    assert {name: system[name] for name in system_channel_values()} == system_channel_values()

    migrated = database.schema + "_migrated"
    try:
        with database.engine.begin() as migrated_connection:
            migrated_connection.execute(text(f'CREATE SCHEMA "{migrated}"'))
            migrated_connection.execute(text(f'SET LOCAL search_path TO "{migrated}"'))
            config.set_main_option("version_table_schema", migrated)
            config.attributes["connection"] = migrated_connection
            command.upgrade(config, "head")
            assert snapshot(migrated_connection, migrated) == actual
    finally:
        connection.exec_driver_sql(f'DROP SCHEMA IF EXISTS "{migrated}" CASCADE')

    # 临时追加真实增量，验证归档可接续升级；不能降到空库来代替增量兼容验证。
    scripts = tmp_path / "alembic"
    copytree("alembic", scripts, ignore=ignore_patterns("__pycache__"))
    (scripts / "versions" / "next_test_revision.py").write_text(
        '"""验证初始基线之后的字段增量。"""\n'
        "from alembic import op\nimport sqlalchemy as sa\n"
        'revision = "test_after_initial"\n'
        f"down_revision = {head!r}\n"
        "def upgrade():\n"
        "    op.add_column('channels', sa.Column('migration_test_value', "
        "sa.String(64), nullable=True, comment='迁移衔接测试字段'))\n"
        "def downgrade():\n"
        "    op.drop_column('channels', 'migration_test_value')\n",
        encoding="utf-8",
    )
    config.set_main_option("script_location", str(scripts))
    with database.engine.begin() as upgrade_connection:
        upgrade_connection.execute(text(f'SET LOCAL search_path TO "{database.schema}"'))
        config.set_main_option("version_table_schema", database.schema)
        config.attributes["connection"] = upgrade_connection
        command.upgrade(config, "head")
        upgraded_system = (
            upgrade_connection.execute(text("SELECT * FROM channels")).mappings().one()
        )
        assert upgraded_system["migration_test_value"] is None
        assert {key: upgraded_system[key] for key in system} == system
        command.downgrade(config, head)
        command.upgrade(config, "head")
        command.downgrade(config, head)
        assert snapshot(upgrade_connection, database.schema) == actual

    # 重复导入失败后，原结构、系统渠道和迁移记录必须完整保留。
    with pytest.raises(ProgrammingError) as failure:
        connection.exec_driver_sql(ARCHIVE.read_text(encoding="utf-8"))
    assert failure.value.orig.sqlstate == "42P07"
    connection.exec_driver_sql("ROLLBACK")
    assert snapshot(connection, database.schema) == actual
    assert connection.execute(text("SELECT * FROM channels")).mappings().one() == system
    assert connection.execute(text("SELECT * FROM creativity_alembic_version")).all() == [
        (head, "system")
    ]


def test_init_sql_rolls_back_partial_ddl_on_conflict(isolated_database):
    connection = isolated_database.connection
    # 在归档后段制造冲突，确保之前已经创建的表和索引也全部回滚。
    connection.exec_driver_sql("CREATE TABLE webhook_endpoints (marker text)")
    connection.exec_driver_sql("INSERT INTO webhook_endpoints VALUES ('原有记录')")
    original = snapshot(connection, isolated_database.schema)
    with pytest.raises(ProgrammingError) as failure:
        connection.exec_driver_sql(ARCHIVE.read_text(encoding="utf-8"))
    assert failure.value.orig.sqlstate == "42P07"
    connection.exec_driver_sql("ROLLBACK")
    assert snapshot(connection, isolated_database.schema) == original
    assert (
        connection.execute(text("SELECT marker FROM webhook_endpoints")).scalar_one() == "原有记录"
    )


async def test_init_sql_supports_channel_and_admin_services(isolated_database):
    database = isolated_database
    database.connection.exec_driver_sql(ARCHIVE.read_text(encoding="utf-8"))
    settings = Settings()
    engine = create_async_engine(
        settings.database_url.get_secret_value(),
        connect_args={"options": f"-csearch_path={database.schema}"},
    )
    redis = Redis.from_url(settings.redis_auth_url.get_secret_value(), socket_timeout=2)
    iam, channels = build_channel_services(engine, redis, database.schema)
    try:
        await channels.channels.initialize_system()
        await channels.channels.initialize_system()
        password = "Sql-init-test-password-1234"
        account = await iam.accounts.initialize_admin(
            AccountCreate(
                login_name="sql-admin", display_name="初始化管理员", initial_password=password
            )
        )
        login = await iam.sessions.login(
            LoginInput(
                login_name="sql-admin",
                password=password,
                captcha_token=await captcha_token(iam, "sql-admin", "sql-init-test"),
            ),
            "sql-init-test",
            new_id("request"),
        )
        session = await iam.authentication.admin_session(
            login.access_token, new_id("request"), allow_initial=True, governance=True
        )
        assert session.account.id == account.user_id
        assert session.account.must_change_password
        assert database.connection.execute(text("SELECT count(*) FROM channels")).scalar_one() == 1
        roles = database.connection.execute(text("SELECT role_code FROM builtin_roles")).scalars()
        assert set(roles) == set(ROLE_NAMES)
    finally:
        keys = [key async for key in redis.scan_iter(f"{database.schema}:*")]
        if keys:
            await redis.delete(*keys)
        await redis.aclose()
        await engine.dispose()
