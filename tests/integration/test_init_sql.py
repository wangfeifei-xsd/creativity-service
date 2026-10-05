"""在真实 PostgreSQL 隔离 schema 验证全量 SQL、迁移衔接及失败回滚。"""

import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from shutil import copytree, ignore_patterns
from types import SimpleNamespace
from uuid import uuid4

import pytest
from alembic.config import Config
from alembic.script import ScriptDirectory
from redis.asyncio import Redis
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.exc import DataError, ProgrammingError
from sqlalchemy.ext.asyncio import create_async_engine

from alembic import command
from creativity_service.core.config import Settings
from creativity_service.core.database.audit import audit_database
from creativity_service.core.primitives import ServiceError, new_id
from creativity_service.modules.channels.assembly import build_channel_services
from creativity_service.modules.channels.initialization import system_channel_values
from creativity_service.modules.iam.custom_roles import CustomRoles
from creativity_service.modules.iam.menus import MenuService
from creativity_service.modules.iam.schemas import AccountCreate, LoginInput, PasswordChange
from creativity_service.storage import metadata
from tests.support.captcha import captcha_token

pytestmark = pytest.mark.integration
ARCHIVE = Path(__file__).resolve().parents[2] / "sql/init.sql"
DATA_ARCHIVE = ARCHIVE.with_name("init_data.sql")
SEED = ARCHIVE.with_name("init_data.json")


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


def test_role_catalog_upgrade_preserves_existing_definitions_and_identity(isolated_database):
    database = isolated_database
    config = Config("alembic.ini")
    config.set_main_option("version_table_schema", database.schema)
    with database.engine.begin() as connection:
        connection.execute(text(f'SET LOCAL search_path TO "{database.schema}"'))
        config.attributes["connection"] = connection
        command.upgrade(config, "0035_management")
        connection.execute(
            text(
                "INSERT INTO builtin_roles "
                "(id, channel_id, role_code, name, allowed_actions, grant_scope, revision) "
                "VALUES ('old-admin-role', 'system', 'channel_admin', '原渠道角色', "
                "'[\"run:read\"]', 'channel', 4)"
            )
        )
        connection.execute(
            text(
                "INSERT INTO custom_roles (id, channel_id, name, allowed_actions, state, revision) "
                "VALUES ('platform-old', 'system', '平台旧角色', "
                "'[\"account:manage\"]', 'ACTIVE', 2), "
                "('channel-old', 'legacy-channel', '渠道旧角色', '[\"run:read\"]', 'ACTIVE', 3)"
            )
        )
        connection.execute(
            text(
                "INSERT INTO platform_accounts (id, channel_id, platform_roles, status) "
                "VALUES ('legacy-account', 'system', '[]', 'ACTIVE')"
            )
        )
        command.upgrade(config, "0036_role_catalog")
        role = (
            connection.execute(
                text(
                    "SELECT * FROM builtin_roles WHERE id = 'old-admin-role' "
                    "AND channel_id = 'system'"
                )
            )
            .mappings()
            .one()
        )
        assert role["name"] == "原渠道角色" and role["allowed_actions"] == ["run:read"]
        assert role["revision"] == 4 and role["account_assignable"] is True
        assert (
            connection.scalar(
                text("SELECT count(*) FROM builtin_roles WHERE channel_id = 'system'")
            )
            == 6
        )
        scopes = dict(connection.execute(text("SELECT id, grant_scope FROM custom_roles")).all())
        assert scopes == {"platform-old": "platform", "channel-old": "channel"}
        account = (
            connection.execute(
                text(
                    "SELECT role_id, platform_roles FROM platform_accounts "
                    "WHERE channel_id = 'system' AND id = 'legacy-account'"
                )
            )
            .mappings()
            .one()
        )
        assert account["role_id"] is None and account["platform_roles"] == []
        assert connection.scalar(text("SELECT count(*) FROM channel_memberships")) == 0


def test_remove_channel_category_preserves_identity_and_related_records(isolated_database):
    database = isolated_database
    config = Config("alembic.ini")
    config.set_main_option("version_table_schema", database.schema)
    with database.engine.begin() as connection:
        connection.execute(text(f'SET LOCAL search_path TO "{database.schema}"'))
        config.attributes["connection"] = connection
        command.upgrade(config, "0036_role_catalog")
        connection.execute(
            text(
                "INSERT INTO channels (id, channel_id, channel_code, name, business_type) "
                "VALUES ('old-channel', 'old-channel', 'OLDX', '既有渠道', 'playmate')"
            )
        )
        related = {
            "channel_code_index": {
                "id": "old-index",
                "channel_id": "system",
                "target_channel_id": "old-channel",
            },
            "data_scopes": {
                "id": "old-scope",
                "channel_id": "old-channel",
                "external_scope_type": "club",
                "external_scope_id": "club-1",
            },
            "service_clients": {"id": "old-client", "channel_id": "old-channel"},
            "channel_keys": {
                "id": "old-key",
                "channel_id": "old-channel",
                "client_id": "old-client",
            },
            "channel_memberships": {
                "id": "old-member",
                "channel_id": "old-channel",
                "user_id": "old-user",
            },
            "resource_grants": {"id": "old-grant", "channel_id": "old-channel"},
            "runs": {"id": "old-run", "channel_id": "old-channel"},
        }
        for table, values in related.items():
            connection.execute(metadata.tables[table].insert().values(**values))
        before = {
            table: connection.execute(metadata.tables[table].select()).mappings().all()
            for table in related
        }
        previous = dict(connection.execute(text("SELECT * FROM channels")).mappings().one())
        previous.pop("business_type")
        command.upgrade(config, "head")
        assert "business_type" not in {
            column["name"]
            for column in inspect(connection).get_columns("channels", schema=database.schema)
        }
        assert dict(connection.execute(text("SELECT * FROM channels")).mappings().one()) == previous
        for table in related:
            assert (
                connection.execute(metadata.tables[table].select()).mappings().all()
                == before[table]
            )
        command.downgrade(config, "0036_role_catalog")
        restored = dict(connection.execute(text("SELECT * FROM channels")).mappings().one())
        assert restored.pop("business_type") is None
        assert restored == previous
        command.upgrade(config, "head")


def test_init_sql_matches_migrations_and_supports_followup_upgrade(isolated_database, tmp_path):
    database = isolated_database
    connection = database.connection
    connection.exec_driver_sql(ARCHIVE.read_text(encoding="utf-8"))
    # 表结构归档不混入系统渠道、角色、账号或迁移版本数据。
    for name in (
        "channels",
        "iam_menus",
        "builtin_roles",
        "platform_accounts",
        "creativity_alembic_version",
    ):
        assert connection.scalar(text(f"SELECT count(*) FROM {name}")) == 0
    connection.exec_driver_sql(DATA_ARCHIVE.read_text(encoding="utf-8"))
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
    expected = json.loads(SEED.read_text())["tables"]
    expected_menus = sorted(expected["iam_menus"], key=lambda row: row["id"])
    initialized_menus = (
        connection.execute(text("SELECT * FROM iam_menus ORDER BY id")).mappings().all()
    )
    assert [
        {key: row[key] for key in expected_menus[0]} for row in initialized_menus
    ] == expected_menus
    for name, rows in expected.items():
        initialized = connection.execute(text(f"SELECT * FROM {name} ORDER BY id")).mappings().all()
        assert [{key: row[key] for key in rows[0]} for row in initialized] == sorted(
            rows, key=lambda row: row["id"]
        )

    migrated = database.schema + "_migrated"
    try:
        with database.engine.begin() as migrated_connection:
            migrated_connection.execute(text(f'CREATE SCHEMA "{migrated}"'))
            migrated_connection.execute(text(f'SET LOCAL search_path TO "{migrated}"'))
            config.set_main_option("version_table_schema", migrated)
            config.attributes["connection"] = migrated_connection
            command.upgrade(config, "head")
            assert snapshot(migrated_connection, migrated) == actual
            migrated_menus = (
                migrated_connection.execute(text("SELECT * FROM iam_menus ORDER BY id"))
                .mappings()
                .all()
            )
            # 历史迁移保留冻结种子；当前环境快照只用于独立数据归档。
            old_seed = (
                ARCHIVE.parents[1] / "src/creativity_service/modules/iam/menu_seed_v0035.json"
            )
            migration_seed = sorted(json.loads(old_seed.read_text()), key=lambda row: row["id"])
            assert [
                {key: row[key] for key in migration_seed[0]} for row in migrated_menus
            ] == migration_seed
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
    database.connection.exec_driver_sql(DATA_ARCHIVE.read_text(encoding="utf-8"))
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
        password = "qwerty123$%^"
        login = await iam.sessions.login(
            LoginInput(
                login_name="admin",
                password=password,
                captcha_token=await captcha_token(iam, "admin", "sql-init-test"),
            ),
            "sql-init-test",
            new_id("request"),
        )
        session = await iam.authentication.admin_session(
            login.access_token, new_id("request"), allow_initial=True, governance=True
        )
        account = json.loads(SEED.read_text())["tables"]["platform_accounts"][0]
        assert session.account.id == account["id"]
        assert session.account.must_change_password
        assert session.account.platform_roles == ["platform_admin"]
        with pytest.raises(ServiceError) as already:
            await iam.accounts.initialize_admin(
                AccountCreate(
                    login_name="other-admin", display_name="管理员", initial_password=password
                )
            )
        assert already.value.code == "ADMIN_ALREADY_INITIALIZED"
        await iam.accounts.change_password(
            session, PasswordChange(current_password=password, new_password="Changed-password-5678")
        )
        login = await iam.sessions.login(
            LoginInput(
                login_name="admin",
                password="Changed-password-5678",
                captcha_token=await captcha_token(iam, "admin", "sql-init-test"),
            ),
            "sql-init-test",
            new_id("request"),
        )
        session = await iam.authentication.admin_session(
            login.access_token, new_id("request"), governance=True
        )
        view = await iam.sessions.view(session)
        assert not session.account.must_change_password
        assert view.can_access_platform
        assert view.workspace_options == []
        assert {"accounts", "roles", "menus"} <= {n.navigation_key for n in view.navigation}
        roles = CustomRoles(iam.access)
        directory = await roles.list(session)
        seeds = {
            row["role_code"]: row for row in json.loads(SEED.read_text())["tables"]["builtin_roles"]
        }
        for row in directory:
            assert row["menu_ids"] == seeds[row["id"]]["menu_ids"]
        assert {row["id"] for row in directory} == {"platform_admin", "channel_admin"}
        # 显式关联既参与导航，也必须阻止删除内置渠道角色正在引用的按钮。
        menus = MenuService(iam.accounts.repository)
        with pytest.raises(ServiceError) as referenced:
            await menus.remove(session, "button_run_read", 1)
        assert referenced.value.code == "MENU_REFERENCED"
        # 角色菜单清单限制入口，但不会替代独立的接口动作鉴权。
        database.connection.execute(
            text(
                "UPDATE builtin_roles SET menu_ids='[\"menu_accounts\"]'::jsonb "
                "WHERE channel_id='system' AND role_code='platform_admin'"
            )
        )
        restricted = await iam.sessions.view(session)
        assert [n.navigation_key for n in restricted.navigation] == ["accounts"]
        assert await menus.list(session)
        assert database.connection.execute(text("SELECT count(*) FROM channels")).scalar_one() == 1
        roles = database.connection.execute(text("SELECT role_code FROM builtin_roles")).scalars()
        assert set(roles) == set(seeds)
        # 即使用户已改密，重复执行数据归档也不回写默认凭据。
        database.connection.exec_driver_sql(DATA_ARCHIVE.read_text(encoding="utf-8"))
        saved = (
            database.connection.execute(text("SELECT * FROM platform_accounts")).mappings().one()
        )
        assert saved["credential_version"] == 2 and saved["must_change_password"] is False
        assert await iam.accounts.passwords.verify("Changed-password-5678", saved["password_hash"])
    finally:
        keys = [key async for key in redis.scan_iter(f"{database.schema}:*")]
        if keys:
            await redis.delete(*keys)
        await redis.aclose()
        await engine.dispose()


def test_initial_data_repeat_preserves_all_records(isolated_database):
    connection = isolated_database.connection
    connection.exec_driver_sql(ARCHIVE.read_text(encoding="utf-8"))
    connection.exec_driver_sql(DATA_ARCHIVE.read_text(encoding="utf-8"))
    connection.execute(
        text("UPDATE iam_menus SET name='已修改菜单' WHERE id='menu_platform-usage'")
    )
    before = {
        name: connection.execute(text(f"SELECT * FROM {name} ORDER BY id")).mappings().all()
        for name in ("channels", "iam_menus", "builtin_roles", "platform_accounts")
    }
    connection.exec_driver_sql(DATA_ARCHIVE.read_text(encoding="utf-8"))
    for name, rows in before.items():
        assert (
            connection.execute(text(f"SELECT * FROM {name} ORDER BY id")).mappings().all() == rows
        )
    assert connection.scalar(text("SELECT count(*) FROM creativity_alembic_version")) == 1


def test_concurrent_initial_data_import_does_not_duplicate_records(isolated_database):
    database = isolated_database
    database.connection.exec_driver_sql(ARCHIVE.read_text(encoding="utf-8"))
    data = DATA_ARCHIVE.read_text(encoding="utf-8")

    def import_data():
        with database.engine.connect().execution_options(
            isolation_level="AUTOCOMMIT"
        ) as connection:
            connection.exec_driver_sql(f'SET search_path TO "{database.schema}"')
            connection.exec_driver_sql(data)

    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [executor.submit(import_data) for _ in range(2)]
        for future in futures:
            future.result()
    for name, rows in json.loads(SEED.read_text())["tables"].items():
        assert database.connection.scalar(text(f"SELECT count(*) FROM {name}")) == len(rows)
    assert database.connection.scalar(text("SELECT count(*) FROM creativity_alembic_version")) == 1


def test_initial_data_failure_rolls_back_data_and_version(isolated_database):
    connection = isolated_database.connection
    connection.exec_driver_sql(ARCHIVE.read_text(encoding="utf-8"))
    data = DATA_ARCHIVE.read_text(encoding="utf-8")
    marker = "-- 初始化数据库角色目录"
    assert marker in data
    broken = data.replace(marker, "SELECT CAST('无效修订' AS BIGINT);\n" + marker, 1)
    with pytest.raises(DataError):
        connection.exec_driver_sql(broken)
    connection.exec_driver_sql("ROLLBACK")
    for name in (
        "channels",
        "iam_menus",
        "builtin_roles",
        "platform_accounts",
        "creativity_alembic_version",
    ):
        assert connection.scalar(text(f"SELECT count(*) FROM {name}")) == 0
    connection.exec_driver_sql(data)
    assert connection.scalar(text("SELECT count(*) FROM platform_accounts")) == 1


def test_builtin_menu_migration_preserves_existing_accounts_and_roles(isolated_database):
    database = isolated_database
    config = Config("alembic.ini")
    config.set_main_option("version_table_schema", database.schema)
    with database.engine.begin() as connection:
        connection.execute(text(f'SET LOCAL search_path TO "{database.schema}"'))
        config.attributes["connection"] = connection
        command.upgrade(config, "0037_remove_business_type")
        connection.execute(
            text(
                "INSERT INTO platform_accounts (id, channel_id, login_name, password_hash, "
                "platform_roles, role_id, status) "
                "VALUES ('existing-admin', 'system', 'admin', '原有摘要', "
                "'[\"platform_admin\"]', NULL, 'ACTIVE')"
            )
        )
        account = connection.execute(text("SELECT * FROM platform_accounts")).mappings().one()
        roles = connection.execute(text("SELECT * FROM builtin_roles ORDER BY id")).mappings().all()
        command.upgrade(config, "head")
        upgraded = (
            connection.execute(text("SELECT * FROM builtin_roles ORDER BY id")).mappings().all()
        )
        assert [dict(row, menu_ids=None) for row in roles] == upgraded
        assert (
            connection.execute(text("SELECT * FROM platform_accounts")).mappings().one() == account
        )
        command.downgrade(config, "0037_remove_business_type")
        assert (
            connection.execute(text("SELECT * FROM builtin_roles ORDER BY id")).mappings().all()
            == roles
        )
        command.upgrade(config, "head")
