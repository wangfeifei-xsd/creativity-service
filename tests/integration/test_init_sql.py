"""在真实 PostgreSQL 隔离 schema 验证全量 SQL、迁移衔接及失败回滚。"""

import asyncio
import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from shutil import copytree, ignore_patterns
from types import SimpleNamespace
from uuid import uuid4

import pytest
from alembic.config import Config
from alembic.script import ScriptDirectory
from httpx import ASGITransport, AsyncClient
from pydantic import SecretStr
from redis.asyncio import Redis
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import DataError, ProgrammingError
from sqlalchemy.ext.asyncio import create_async_engine

from alembic import command
from creativity_service.app import create_app
from creativity_service.core.config import Settings
from creativity_service.core.context import AuthContext, Scope
from creativity_service.core.contracts import Admission
from creativity_service.core.database import transaction
from creativity_service.core.database.audit import audit_database
from creativity_service.core.deletion.ledger import DeletionLedger
from creativity_service.core.primitives import ServiceError, new_id
from creativity_service.modules.budgets.services import BudgetService
from creativity_service.modules.channels.assembly import build_channel_services
from creativity_service.modules.channels.initialization import system_channel_values
from creativity_service.modules.channels.schemas import ChannelCreate
from creativity_service.modules.data_lifecycle.recovery import backup_manifest
from creativity_service.modules.iam.custom_roles import CustomRoles
from creativity_service.modules.iam.menus import MenuService
from creativity_service.modules.iam.schemas import (
    AccountCreate,
    ChannelContextInput,
    LoginInput,
    PasswordChange,
)
from creativity_service.modules.models.assembly import ModelSettings, build_model_services
from creativity_service.modules.models.schemas import ProviderView
from creativity_service.modules.usage.assembly import build_usage_services
from creativity_service.storage import metadata
from scripts.render_weather_seed import load_weather, merged_tables, typed_archive_values
from tests.support.captcha import captcha_token

pytestmark = pytest.mark.integration
ARCHIVE = Path(__file__).resolve().parents[2] / "sql/init.sql"
DATA_ARCHIVE = ARCHIVE.with_name("init_data.sql")
SEED = ARCHIVE.with_name("init_data.json")


def archived_tables():
    """实际交付数据同时包含基础种子与成功天气链路。"""
    base = json.loads(SEED.read_text())["tables"]
    return merged_tables(base, load_weather(base)["tables"])


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
        command.upgrade(config, "0037_remove_business_type")
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
        command.upgrade(config, "0037_remove_business_type")


def test_init_sql_matches_migrations_and_supports_followup_upgrade(isolated_database, tmp_path):
    database = isolated_database
    connection = database.connection
    connection.exec_driver_sql(ARCHIVE.read_text(encoding="utf-8"))
    # 表结构归档不混入渠道、供应商、账号授权或迁移版本数据。
    for name in (*archived_tables(), "creativity_alembic_version"):
        assert connection.scalar(text(f"SELECT count(*) FROM {name}")) == 0
    connection.exec_driver_sql(
        DATA_ARCHIVE.read_text(encoding="utf-8"), execution_options={"no_parameters": True}
    )
    assert audit_database(connection, database.schema) == []
    actual = snapshot(connection, database.schema)
    assert set(actual) == {*metadata.tables, "creativity_alembic_version"}
    config = Config("alembic.ini")
    head = ScriptDirectory.from_config(config).get_current_head()
    assert connection.execute(text("SELECT * FROM creativity_alembic_version")).all() == [
        (head, "system")
    ]
    system = (
        connection.execute(text("SELECT * FROM channels WHERE channel_id='system' AND id='system'"))
        .mappings()
        .one()
    )
    assert system["id"] == system["channel_id"] == "system"
    assert system["revision"] == 1
    assert system["created_at"] == system["updated_at"]
    assert {name: system[name] for name in system_channel_values()} == system_channel_values()
    expected = archived_tables()
    expected_menus = sorted(expected["iam_menus"], key=lambda row: row["id"])
    initialized_menus = (
        connection.execute(text("SELECT * FROM iam_menus ORDER BY id")).mappings().all()
    )
    assert [
        {key: row[key] for key in expected_menus[0]} for row in initialized_menus
    ] == expected_menus
    for name, rows in expected.items():
        initialized = connection.execute(text(f"SELECT * FROM {name} ORDER BY id")).mappings().all()
        # 每条冻结记录分别核对字段，天气记录保留真实时间，基础数据仍用导入时间。
        expected_by_id = {row["id"]: row for row in rows}
        assert {row["id"] for row in initialized} == expected_by_id.keys()
        for row in initialized:
            source = expected_by_id[row["id"]]
            typed = typed_archive_values(metadata.tables[name], source)
            assert {key: row[key] for key in source} == typed
    limit = connection.execute(text("SELECT * FROM platform_limits")).mappings().one()
    assert limit["effective_at"] == limit["created_at"] == limit["updated_at"]
    assert limit["effective_at"] <= connection.scalar(text("SELECT CURRENT_TIMESTAMP"))

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
                ARCHIVE.parents[1] / "src/creativity_service/modules/iam/menu_seed_v0041.json"
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
            upgrade_connection.execute(
                text("SELECT * FROM channels WHERE channel_id='system' AND id='system'")
            )
            .mappings()
            .one()
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
    assert (
        connection.execute(text("SELECT * FROM channels WHERE channel_id='system' AND id='system'"))
        .mappings()
        .one()
        == system
    )
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
    database.connection.exec_driver_sql(
        DATA_ARCHIVE.read_text(encoding="utf-8"), execution_options={"no_parameters": True}
    )
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
        assert [option.environment for option in view.workspace_options] == ["dev"]
        assert {"accounts", "roles", "menus"} <= {n.navigation_key for n in view.navigation}
        page = await channels.channels.list_page(session, search="寻弈乐竞", status="ACTIVE")
        assert page.total == len(page.items) == 1
        channel = page.items[0]
        assert channel.name == "寻弈乐竞" and channel.owner == "小苏打"
        assert channel.channel_code == "XYLJ" and channel.status_label == "启用"
        detail = await channels.channels.detail(session, channel.channel_id)
        # 列表额外带配置汇总，详情比较渠道主档和可操作范围。
        assert detail.model_dump(exclude={"configuration_status"}) == channel.model_dump(
            exclude={"configuration_status"}
        )
        async with engine.connect() as connection:
            pending = await iam.access.first_administrator(connection, channel.channel_id)
        assert pending == {
            "user_id": account["id"],
            "display_name": account["display_name"],
            "environments": ["dev"],
            "initial_environments": ["dev"],
        }
        assert [
            e.environment for e in await channels.channels.environments(session, channel.channel_id)
        ] == ["dev"]
        with pytest.raises(ServiceError) as duplicate:
            await channels.channels.create(
                session,
                ChannelCreate(name="寻弈乐竞", owner="小苏打", first_admin_user_id=account["id"]),
            )
        assert duplicate.value.code == "CODE_EXISTS"
        models = build_model_services(engine, iam, settings=ModelSettings(_env_file=None))
        providers = await models.configuration.providers(session)
        expected_providers = json.loads(SEED.read_text())["tables"]["provider_catalog"]
        assert sorted(
            [provider.model_dump() for provider in providers], key=lambda row: row["id"]
        ) == sorted(
            [{key: row[key] for key in ProviderView.model_fields} for row in expected_providers],
            key=lambda row: row["id"],
        )
        usage = build_usage_services(engine, channels.channels)
        limits = await usage.management.platform_limits(session)
        assert len(limits) == 1
        assert limits[0].limit_code == limits[0].unit == "concurrency"
        assert limits[0].limit_value == 20 and limits[0].status == "ACTIVE"
        assert (limits[0].used, limits[0].remaining) == (0, 20)
        for name in (
            "service_clients",
            "channel_keys",
        ):
            assert database.connection.scalar(text(f"SELECT count(*) FROM {name}")) == 0
        for name in ("model_connections", "models", "credentials"):
            assert database.connection.scalar(text(f"SELECT count(*) FROM {name}")) == 1
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
        assert database.connection.execute(text("SELECT count(*) FROM channels")).scalar_one() == 2
        roles = database.connection.execute(text("SELECT role_code FROM builtin_roles")).scalars()
        assert set(roles) == set(seeds)
        # 即使用户已改密，重复执行数据归档也不回写默认凭据。
        database.connection.exec_driver_sql(
            DATA_ARCHIVE.read_text(encoding="utf-8"), execution_options={"no_parameters": True}
        )
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


async def test_initialized_admin_pages_and_content_boundaries(
    isolated_database, tmp_path, monkeypatch
):
    database = isolated_database
    database.connection.exec_driver_sql(ARCHIVE.read_text())
    database.connection.exec_driver_sql(
        DATA_ARCHIVE.read_text(), execution_options={"no_parameters": True}
    )
    monkeypatch.setenv("CREATIVITY_DELETION_LEDGER_PATH", str(tmp_path / "ledger"))
    settings = Settings()
    url = make_url(settings.database_url.get_secret_value()).update_query_dict(
        {"options": f"-csearch_path={database.schema}"}
    )
    settings = settings.model_copy(
        update={
            "database_url": SecretStr(url.render_as_string(hide_password=False)),
            "redis_key_prefix": database.schema,
            "log_directory": tmp_path / "log",
            "otel_enabled": False,
        }
    )
    app = create_app(settings)
    async with app.router.lifespan_context(app):
        iam = app.state.iam
        redis = app.state.infrastructure.redis_clients["redis_auth"]
        try:
            channel = archived_tables()["channel_environments"][0]["channel_id"]
            # 与部署步骤一致：独立删除清单不由纯 SQL 写入。
            await backup_manifest(app.state.data_lifecycle, channel)
            password = "qwerty123$%^"
            login = await iam.sessions.login(
                LoginInput(
                    login_name="admin",
                    password=password,
                    captcha_token=await captcha_token(iam, "admin", "page-init-test"),
                ),
                "page-init-test",
                new_id("request"),
            )
            session = await iam.authentication.admin_session(
                login.access_token, new_id("request"), allow_initial=True, governance=True
            )
            await iam.accounts.change_password(
                session,
                PasswordChange(current_password=password, new_password="Changed-password-5678"),
            )
            login = await iam.sessions.login(
                LoginInput(
                    login_name="admin",
                    password="Changed-password-5678",
                    captcha_token=await captcha_token(iam, "admin", "page-init-test"),
                ),
                "page-init-test",
                new_id("request"),
            )
            session = await iam.authentication.admin_session(
                login.access_token, new_id("request"), governance=True
            )
            token = await iam.sessions.enter(
                session, ChannelContextInput(channel_id=channel, environment="dev")
            )
            paths = [
                "models",
                "model-routes",
                "skills",
                "conversations",
                "memories",
                "memory-subjects",
            ]
            async with AsyncClient(
                transport=ASGITransport(app=app),
                base_url="http://test",
                headers={"Authorization": f"Bearer {token.access_token}"},
            ) as client:
                for path in paths:
                    response = await client.get(f"/admin/v1/{path}")
                    assert response.status_code == 200, (path, response.text)
                    if path in {"models", "model-routes", "skills"}:
                        assert response.json()["items"]
                # 补齐初始化数据不能绕过独立恢复封锁。
                await DeletionLedger().operate(channel, blocked=True, required=True)
                response = await client.get("/admin/v1/skills")
                assert response.status_code == 503
                assert response.json()["error"]["code"] == "RECOVERY_BLOCKED"
                await DeletionLedger().operate(channel, blocked=False, required=True)
                # 移除显式授权后仍拒绝敏感原文，内置管理员角色不被整体扩权。
                database.connection.execute(
                    text(
                        "DELETE FROM resource_grants "
                        "WHERE resource_type IN ('conversation', 'memory')"
                    )
                )
                for path in ("conversations", "memories", "memory-subjects"):
                    response = await client.get(f"/admin/v1/{path}")
                    assert response.status_code == 403, (path, response.text)
                response = await client.get("/admin/v1/skills")
                assert response.status_code == 200
        finally:
            keys = [key async for key in redis.scan_iter(f"{database.schema}:*")]
            if keys:
                await redis.delete(*keys)


def test_initial_data_repeat_preserves_all_records(isolated_database):
    connection = isolated_database.connection
    connection.exec_driver_sql(ARCHIVE.read_text(encoding="utf-8"))
    connection.exec_driver_sql(
        DATA_ARCHIVE.read_text(encoding="utf-8"), execution_options={"no_parameters": True}
    )
    connection.execute(
        text("UPDATE iam_menus SET name='已修改菜单' WHERE id='menu_platform-usage'")
    )
    connection.execute(
        text(
            "UPDATE channels SET name='已修改渠道', owner='新负责人', revision=2 "
            "WHERE channel_code='XYLJ' AND channel_id != 'system'"
        )
    )
    connection.execute(text("UPDATE platform_limits SET limit_value=30, revision=2"))
    connection.execute(text("UPDATE budget_policies SET limit_value=8, status='DISABLED'"))
    providers = metadata.tables["provider_catalog"]
    connection.execute(
        providers.update()
        .where(providers.c.channel_id == "system", providers.c.id == "provider_deepseek")
        .values(
            name="已修改供应商",
            template_content={
                "protocol": "chat_completions",
                "endpoint": "https://models.example/v1",
                "timeout_seconds": 120,
            },
            revision=2,
        )
    )
    before = {
        name: connection.execute(text(f"SELECT * FROM {name} ORDER BY id")).mappings().all()
        for name in json.loads(SEED.read_text())["tables"]
    }
    connection.exec_driver_sql(
        DATA_ARCHIVE.read_text(encoding="utf-8"), execution_options={"no_parameters": True}
    )
    for name, rows in before.items():
        assert (
            connection.execute(text(f"SELECT * FROM {name} ORDER BY id")).mappings().all() == rows
        )
    assert connection.scalar(text("SELECT count(*) FROM creativity_alembic_version")) == 1


@pytest.mark.parametrize(
    ("capacity", "error_code"), [(5, "BUDGET_EXCEEDED"), (20, "PLATFORM_LIMIT_EXCEEDED")]
)
async def test_initialized_concurrency_limits_enforce_admission_and_release(
    isolated_database, capacity, error_code
):
    database = isolated_database
    connection = database.connection
    connection.exec_driver_sql(ARCHIVE.read_text(encoding="utf-8"))
    connection.exec_driver_sql(
        DATA_ARCHIVE.read_text(encoding="utf-8"), execution_options={"no_parameters": True}
    )
    if capacity == 20:
        # 仅在本测试 schema 禁用渠道策略，独立验证平台边界及失败整批回滚。
        connection.execute(text("UPDATE budget_policies SET status='DISABLED'"))
    channel_id = connection.scalar(text("SELECT id FROM channels WHERE channel_id != 'system'"))
    # 直接验证准入层；不为预置渠道写入环境、数据域或业务访问授权。
    context = AuthContext(
        scope=Scope(channel_id=channel_id, environment="dev"),
        principal_type="worker",
        principal_id="test-worker",
        request_id=new_id("request"),
    )
    engine = create_async_engine(
        Settings().database_url.get_secret_value(),
        connect_args={"options": f"-csearch_path={database.schema}"},
    )
    budgets = BudgetService(engine)

    async def admit(run_id):
        async with transaction(
            engine, context.scope, budgets.admission_keys(context, run_id)
        ) as uow:
            return await budgets.admit(uow, context, run_id)

    def assert_occupancy():
        archived_ids = {row["id"] for row in archived_tables()["admissions"]}
        admissions = [
            row
            for row in connection.execute(text("SELECT * FROM admissions")).mappings()
            if row["id"] not in archived_ids
        ]
        occupancies = (
            connection.execute(text("SELECT * FROM platform_quota_occupancies")).mappings().all()
        )
        held = [row for row in admissions if row["status"] == "HELD"]
        platform_held = [row for row in occupancies if row["status"] == "HELD"]
        assert len(held) == len(platform_held) == capacity
        assert {row["run_id"] for row in held} == {row["run_id"] for row in platform_held}
        assert all(row["channel_id"] == channel_id for row in admissions)
        assert all(
            row["channel_id"] == "system"
            and row["target_channel_id"] == channel_id
            and row["limit_code"] == "concurrency"
            for row in occupancies
        )
        assert len(admissions) == len(occupancies)
        if capacity == 5:
            policy = connection.execute(text("SELECT * FROM budget_policies")).mappings().one()
            assert all(
                row["policy_refs"] == [{"id": policy["id"], "version_id": policy["version_id"]}]
                for row in admissions
            )
        return admissions

    try:
        runs = [new_id("run") for _ in range(capacity + 1)]
        results = await asyncio.gather(*(admit(run_id) for run_id in runs), return_exceptions=True)
        accepted = [result for result in results if isinstance(result, Admission)]
        rejected = [result for result in results if isinstance(result, ServiceError)]
        assert len(accepted) == capacity and len(rejected) == 1
        assert rejected[0].code == error_code and rejected[0].status == 429
        assert len(assert_occupancy()) == capacity
        await budgets.finish_admission(context, accepted[0].run_id)
        assert connection.scalar(text("SELECT count(*) FROM admissions WHERE status='HELD'")) == (
            capacity - 1
        )
        assert connection.scalar(
            text("SELECT count(*) FROM platform_quota_occupancies WHERE status='HELD'")
        ) == (capacity - 1)
        replacement = await admit(new_id("run"))
        assert isinstance(replacement, Admission)
        assert len(assert_occupancy()) == capacity + 1
        with pytest.raises(ServiceError) as exceeded:
            await admit(new_id("run"))
        assert exceeded.value.code == error_code
        assert len(assert_occupancy()) == capacity + 1
    finally:
        await engine.dispose()


def test_concurrent_initial_data_import_does_not_duplicate_records(isolated_database):
    database = isolated_database
    database.connection.exec_driver_sql(ARCHIVE.read_text(encoding="utf-8"))
    data = DATA_ARCHIVE.read_text(encoding="utf-8")

    def import_data():
        with database.engine.connect().execution_options(
            isolation_level="AUTOCOMMIT"
        ) as connection:
            connection.exec_driver_sql(f'SET search_path TO "{database.schema}"')
            connection.exec_driver_sql(data, execution_options={"no_parameters": True})

    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [executor.submit(import_data) for _ in range(2)]
        for future in futures:
            future.result()
    for name, rows in archived_tables().items():
        assert database.connection.scalar(text(f"SELECT count(*) FROM {name}")) == len(rows)
    assert database.connection.scalar(text("SELECT count(*) FROM creativity_alembic_version")) == 1


@pytest.mark.parametrize("marker", ["-- 初始化数据库角色目录", "-- 最后登记迁移完成标记"])
def test_initial_data_failure_rolls_back_data_and_version(isolated_database, marker):
    connection = isolated_database.connection
    connection.exec_driver_sql(ARCHIVE.read_text(encoding="utf-8"))
    data = DATA_ARCHIVE.read_text(encoding="utf-8")
    assert marker in data
    broken = data.replace(marker, "SELECT CAST('无效修订' AS BIGINT);\n" + marker, 1)
    with pytest.raises(DataError):
        connection.exec_driver_sql(broken, execution_options={"no_parameters": True})
    connection.exec_driver_sql("ROLLBACK")
    for name in (*archived_tables(), "creativity_alembic_version"):
        assert connection.scalar(text(f"SELECT count(*) FROM {name}")) == 0
    connection.exec_driver_sql(data, execution_options={"no_parameters": True})
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
        command.upgrade(config, "0038_builtin_role_menus")
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
        command.upgrade(config, "0038_builtin_role_menus")
