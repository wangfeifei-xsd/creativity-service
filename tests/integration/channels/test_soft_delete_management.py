"""菜单与角色管理删除保留记录，并排除选择器、重复检查和后续修改。"""

import pytest
from sqlalchemy import select

from creativity_service.core.primitives import ServiceError
from creativity_service.modules.iam.custom_roles import CustomRoles, RoleSave
from creativity_service.modules.iam.menus import MenuSave, MenuService, menu_rows
from creativity_service.modules.iam.repositories import TABLES

from .conftest import provision
from .test_grant_actions import grant_input
from .test_member_actions import add_member

pytestmark = pytest.mark.integration


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
