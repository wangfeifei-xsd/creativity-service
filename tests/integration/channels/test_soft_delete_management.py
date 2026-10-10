"""菜单与角色管理删除保留记录，并排除选择器、重复检查和后续修改。"""

import pytest
from sqlalchemy import select, update

from creativity_service.core.database import transaction
from creativity_service.core.locking import read_key
from creativity_service.core.primitives import ServiceError
from creativity_service.modules.agents.access import read_locked_policy
from creativity_service.modules.iam.custom_roles import CustomRoles, RoleSave
from creativity_service.modules.iam.menus import MenuSave, MenuService, menu_rows
from creativity_service.modules.iam.repositories import TABLES, policy_key
from creativity_service.modules.iam.schemas import AuditFilter

from .conftest import provision
from .test_grant_actions import grant_input
from .test_member_actions import add_member

pytestmark = pytest.mark.integration


@pytest.mark.parametrize("removed_table", ["platform_accounts", "channel_memberships"])
@pytest.mark.filterwarnings("error::sqlalchemy.exc.SAWarning")
async def test_locked_management_policy_excludes_deleted_identity(channel_env, removed_table):
    env = channel_env
    tenant = await provision(env)
    context = tenant.manager.context
    keys = [read_key(policy_key(context.scope.channel_id)), read_key(policy_key("system"))]
    async with transaction(env.engine, context.scope, keys) as uow:
        policy = await read_locked_policy(uow, context)
        policy.require(context, "agent:manage", "agent", "new")
    table = TABLES[removed_table]
    condition = (
        (table.c.channel_id == "system") & (table.c.id == env.user_id)
        if removed_table == "platform_accounts"
        else (table.c.channel_id == context.scope.channel_id) & (table.c.user_id == env.user_id)
    )
    # 模拟历史身份被逻辑删除，验证锁内授权不会读取已删除记录。
    async with env.engine.begin() as connection:
        await connection.execute(update(table).where(condition).values(is_deleted=True))
    async with transaction(env.engine, context.scope, keys) as uow:
        with pytest.raises(ServiceError) as denied:
            await read_locked_policy(uow, context)
        assert denied.value.code == "FORBIDDEN"


async def test_menu_and_role_deletion_preserves_rows_and_hides_management_entries(channel_env):
    env = channel_env
    menus = MenuService(env.iam.accounts.repository)
    menu = await menus.save(env.admin, MenuSave(name="删除测试目录", kind="DIR"))
    roles = CustomRoles(env.iam.access)
    body = RoleSave(name="删除测试角色", allowed_actions=["run:read"], grant_scope="channel")
    role = await roles.save(env.admin, body)
    await menus.remove(env.admin, menu["id"], menu["revision"])
    await roles.remove(env.admin, role["id"], role["revision"])
    async with env.engine.connect() as connection:
        assert menu["id"] not in {r["id"] for r in await menu_rows(connection)}
        for name, original in (("iam_menus", menu), ("custom_roles", role)):
            table = TABLES[name]
            deleted = (
                (
                    await connection.execute(
                        select(table).where(
                            table.c.channel_id == "system", table.c.id == original["id"]
                        )
                    )
                )
                .mappings()
                .one()
            )
            assert deleted["is_deleted"] is True
            assert deleted["name"] == original["name"]
            assert deleted["revision"] == original["revision"] + 1
    assert role["id"] not in {r["id"] for r in await roles.list(env.admin)}
    assert role["id"] not in {r.role_code for r in await env.iam.accounts.role_options(env.admin)}
    # 模拟未保存名称快照的历史事件，审计应从逻辑删除记录解析名称。
    audit_table = TABLES["audit_events"]
    async with env.engine.begin() as connection:
        await connection.execute(
            update(audit_table)
            .where(
                audit_table.c.channel_id == "system",
                audit_table.c.target_id.in_([menu["id"], role["id"]]),
            )
            .values(summary={})
        )
    events = await env.iam.audit.page(env.admin, AuditFilter(limit=200))
    for original in (menu, role):
        matching = [event for event in events.items if event.target_id == original["id"]]
        assert matching
        assert all(event.target_name == f"{original['name']}（已删除）" for event in matching)
    with pytest.raises(ServiceError) as removed:
        await menus.save(env.admin, MenuSave(name="不能恢复", kind="DIR", revision=2), menu["id"])
    assert removed.value.status == 404
    with pytest.raises(ServiceError):
        await roles.save(env.admin, body.model_copy(update={"revision": 2}), role["id"])
    replacement = await menus.save(env.admin, MenuSave(name="删除测试目录", kind="DIR"))
    assert replacement["id"] != menu["id"]


async def test_removed_members_require_explicit_reauthorization_and_grants_keep_tombstones(
    channel_env,
):
    env = channel_env
    tenant = await provision(env)
    access, channel_id = env.iam.access, tenant.channel.channel_id
    member, body = await add_member(env, tenant)
    await access.remove_member(tenant.manager, channel_id, member.user_id, member.revision)
    table = TABLES["channel_memberships"]
    async with env.engine.connect() as connection:
        deleted = (
            (
                await connection.execute(
                    select(table).where(
                        table.c.channel_id == channel_id, table.c.user_id == member.user_id
                    )
                )
            )
            .mappings()
            .one()
        )
    assert deleted["is_deleted"] is True
    assert deleted["revision"] == member.revision + 1
    with pytest.raises(ServiceError) as stale:
        await access.put_member(
            tenant.manager,
            channel_id,
            member.user_id,
            body.model_copy(update={"revision": member.revision}),
        )
    assert stale.value.status == 404
    restored = await access.put_member(tenant.manager, channel_id, member.user_id, body)
    assert restored.revision == deleted["revision"] + 1
    assert member.user_id in {
        row.user_id for row in await access.list_members(tenant.manager, channel_id)
    }
    grant_body = grant_input(tenant)
    grant = await access.put_grant(tenant.manager, channel_id, "removed_grant", grant_body)
    await access.revoke_grant(tenant.manager, channel_id, grant.grant_id, grant.revision)
    table = TABLES["resource_grants"]
    async with env.engine.connect() as connection:
        deleted_grant = (
            (
                await connection.execute(
                    select(table).where(
                        table.c.channel_id == channel_id, table.c.id == grant.grant_id
                    )
                )
            )
            .mappings()
            .one()
        )
    assert deleted_grant["is_deleted"] is True
    assert deleted_grant["allowed_actions"] == []
    with pytest.raises(ServiceError) as stale_grant:
        await access.put_grant(tenant.manager, channel_id, grant.grant_id, grant_body)
    assert stale_grant.value.status == 404
    replacement = await access.put_grant(tenant.manager, channel_id, "new_grant", grant_body)
    assert replacement.grant_id != grant.grant_id
