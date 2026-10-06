"""管理闭环验证：菜单树、平台角色、分页范围与审计的真实数据库行为。"""

import asyncio
from datetime import timedelta

import pytest
from sqlalchemy import insert, select

from creativity_service.core.primitives import ServiceError, new_id, utcnow
from creativity_service.modules.iam.custom_roles import CustomRoles, RoleSave
from creativity_service.modules.iam.menus import MenuSave, MenuService
from creativity_service.modules.iam.schemas import (
    AccountCreate,
    AccountUpdate,
    AuditFilter,
    LoginInput,
    PasswordChange,
    PasswordReset,
)
from creativity_service.storage import metadata
from tests.integration.channels.conftest import INITIAL, PASSWORD, provision
from tests.support.captcha import captcha_token

pytestmark = pytest.mark.integration


async def test_role_options_keep_platform_capabilities_and_remove_channel_only_groups(channel_env):
    env = channel_env
    roles = CustomRoles(env.iam.access)
    platform = await roles.options(env.admin, "platform")
    channel = await roles.options(env.admin, "channel")
    platform_pages = {row["page_key"] for row in platform["menus"] if row["kind"] == "MENU"}
    assert platform_pages == {
        "model-providers",
        "channels",
        "accounts",
        "roles",
        "menus",
        "platform-limits",
        "platform-usage",
        "audit-events",
    }
    excluded = {"menu_group_execution", "menu_group_integration"}
    assert not excluded & {row["id"] for row in platform["menus"]}
    assert excluded <= {row["id"] for row in channel["menus"]}
    builtin = next(row for row in await roles.list(env.admin) if row["id"] == "platform_admin")
    assert builtin["menu_ids"] is None or {row["id"] for row in platform["menus"]} <= set(
        builtin["menu_ids"]
    )
    # 多层空目录同样移除；一旦包含平台页面，全部祖先都应出现在选项树中。
    menus = MenuService(env.iam.accounts.repository)
    outer = await menus.save(env.admin, MenuSave(name="分组验证", kind="DIR"))
    inner = await menus.save(env.admin, MenuSave(name="子目录", kind="DIR", parent_id=outer["id"]))
    assert not {outer["id"], inner["id"]} & {
        row["id"] for row in (await roles.options(env.admin, "platform"))["menus"]
    }
    usage = next(row for row in platform["menus"] if row["page_key"] == "platform-usage")
    await menus.save(
        env.admin,
        MenuSave(**{key: usage[key] for key in MenuSave.model_fields}).model_copy(
            update={"parent_id": inner["id"]}
        ),
        usage["id"],
    )
    assert {outer["id"], inner["id"]} <= {
        row["id"] for row in (await roles.options(env.admin, "platform"))["menus"]
    }


async def signed_account(env, name, roles):
    account = await env.iam.accounts.create(
        env.admin,
        AccountCreate(
            login_name=name,
            display_name="管理验证账号",
            initial_password=INITIAL,
            platform_roles=roles,
        ),
    )

    async def login(password, initial=False):
        issued = await env.iam.sessions.login(
            LoginInput(
                login_name=name,
                password=password,
                captcha_token=await captcha_token(env.iam, name, "directory-test"),
            ),
            "directory-test",
            new_id("request"),
        )
        session = await env.iam.authentication.admin_session(
            issued.access_token,
            new_id("request"),
            allow_initial=initial,
        )
        return issued, session

    _, initial = await login(INITIAL, True)
    await env.iam.accounts.change_password(
        initial, PasswordChange(current_password=INITIAL, new_password=PASSWORD)
    )
    issued, session = await login(PASSWORD)
    return account, issued, session


async def test_menu_tree_mutation_revision_protection_and_role_reference(channel_env):
    env = channel_env
    menus = MenuService(env.iam.accounts.repository)
    roles = CustomRoles(env.iam.access)
    catalog = await menus.list(env.admin)
    usage = next(r for r in catalog if r["page_key"] == "platform-usage")
    fields = MenuSave.model_fields
    usage_body = MenuSave(**{key: usage[key] for key in fields})
    directory = await menus.save(env.admin, MenuSave(name="统计工作台", kind="DIR"))
    changed = await menus.save(
        env.admin,
        usage_body.model_copy(
            update={
                "parent_id": directory["id"],
                "name": "渠道对比",
                "sort_order": 1,
            }
        ),
        usage["id"],
    )
    session = await env.iam.sessions.view(env.admin)
    item = next(n for n in session.navigation if n.navigation_key == "platform-usage")
    assert item.label == "渠道对比" and item.ancestors[-1].label == "统计工作台"
    with pytest.raises(ServiceError) as conflict:
        await menus.save(env.admin, usage_body, usage["id"])
    assert conflict.value.code == "REVISION_CONFLICT"
    with pytest.raises(ServiceError) as cycle:
        await menus.save(
            env.admin,
            MenuSave(
                name="统计工作台",
                kind="DIR",
                revision=directory["revision"],
                parent_id=directory["id"],
            ),
            directory["id"],
        )
    assert cycle.value.code == "MENU_PARENT_INVALID"
    protected = next(r for r in catalog if r["page_key"] == "accounts")
    with pytest.raises(ServiceError) as forbidden:
        await menus.remove(env.admin, protected["id"], protected["revision"])
    assert forbidden.value.code == "MENU_PROTECTED"
    with pytest.raises(ServiceError):
        await menus.save(env.admin, MenuSave(name="未知页面", kind="MENU", page_key="arbitrary"))
    role = await roles.save(
        env.admin,
        RoleSave(name="统计观察员", allowed_actions=["usage:platform"], menu_ids=[usage["id"]]),
    )
    with pytest.raises(ServiceError) as referenced:
        await menus.remove(env.admin, usage["id"], changed["revision"])
    assert referenced.value.code == "MENU_REFERENCED"
    await roles.remove(env.admin, role["id"], role["revision"])
    await menus.save(
        env.admin, usage_body.model_copy(update={"revision": changed["revision"]}), usage["id"]
    )
    await menus.remove(env.admin, directory["id"], directory["revision"])


async def test_platform_role_live_permissions_menu_visibility_and_no_account_takeover(channel_env):
    env = channel_env
    roles = CustomRoles(env.iam.access)
    menu = next(
        r for r in (await roles.options(env.admin))["menus"] if r["page_key"] == "platform-usage"
    )
    role = await roles.save(
        env.admin,
        RoleSave(
            name="受限平台运营",
            allowed_actions=[
                "usage:platform",
                "account:manage",
                "role:grant",
            ],
            menu_ids=[menu["id"]],
        ),
    )
    account, token, session = await signed_account(env, "restricted-admin", [role["id"]])
    view = await env.iam.sessions.view(session)
    assert [n.navigation_key for n in view.navigation] == ["platform-usage"]
    channel = await provision(env)
    choices = await env.services.channels.usage_channels(session, "租号", 0, 20)
    assert choices.items == [{"value": channel.channel.channel_id, "label": "租号渠道"}]
    root = await env.iam.accounts.get(env.admin, env.user_id)
    with pytest.raises(ServiceError) as elevated:
        await env.iam.accounts.update(
            session,
            env.user_id,
            AccountUpdate(
                revision=root.revision,
                platform_roles=[],
            ),
        )
    assert elevated.value.status == 403
    with pytest.raises(ServiceError):
        await env.iam.accounts.reset(
            session,
            env.user_id,
            PasswordReset(
                revision=root.revision,
                initial_password=INITIAL,
            ),
        )
    with pytest.raises(ServiceError) as last:
        await env.iam.accounts.update(
            env.admin,
            env.user_id,
            AccountUpdate(
                revision=root.revision,
                status="DISABLED",
            ),
        )
    assert last.value.code == "LAST_PLATFORM_ADMIN"
    with pytest.raises(ServiceError) as bound:
        await roles.remove(env.admin, role["id"], role["revision"])
    assert bound.value.code == "ROLE_REFERENCED"
    outcomes = await asyncio.gather(
        *[
            roles.save(
                env.admin,
                RoleSave(
                    name="受限平台运营",
                    allowed_actions=["usage:platform"],
                    menu_ids=[menu["id"]],
                    revision=role["revision"],
                    active=False,
                ),
                role["id"],
            )
            for _ in range(2)
        ],
        return_exceptions=True,
    )
    assert sum(isinstance(r, ServiceError) and r.code == "REVISION_CONFLICT" for r in outcomes) == 1
    auth = {"Authorization": "Bearer " + token.access_token}
    assert (await env.client.get("/admin/v1/accounts/page", headers=auth)).status_code == 403
    assert (await env.iam.sessions.view(session)).navigation == []
    assert account.platform_role_names == ["受限平台运营"]


async def test_more_than_two_hundred_rows_are_searchable_and_paginated(channel_env):
    env = channel_env
    tenant = await provision(env)
    async with env.engine.begin() as connection:
        accounts, channels, audit = [
            metadata.tables[t] for t in ("platform_accounts", "channels", "audit_events")
        ]
        account = dict(
            (await connection.execute(select(accounts).where(accounts.c.id == env.user_id)))
            .mappings()
            .one()
        )
        channel = dict(
            (
                await connection.execute(
                    select(channels).where(channels.c.id == tenant.channel.channel_id)
                )
            )
            .mappings()
            .one()
        )
        now = utcnow()
        await connection.execute(
            insert(accounts),
            [
                {
                    **account,
                    "id": f"page_user_{i:03}",
                    "login_name": f"page-{i:03}",
                    "display_name": f"分页账号{i:03}",
                    "platform_roles": [],
                }
                for i in range(205)
            ],
        )
        await connection.execute(
            insert(channels),
            [
                {
                    **channel,
                    "id": f"page_channel_{i:03}",
                    "channel_id": f"page_channel_{i:03}",
                    "channel_code": f"page-{i:03}",
                    "name": f"分页渠道{i:03}",
                }
                for i in range(205)
            ],
        )
        await connection.execute(
            insert(metadata.tables["channel_code_index"]),
            [
                {
                    "id": f"index_{i:03}",
                    "channel_id": "system",
                    "channel_code": f"page-{i:03}",
                    "target_channel_id": f"page_channel_{i:03}",
                    "created_at": now,
                    "updated_at": now,
                    "revision": 1,
                }
                for i in range(205)
            ],
        )
        await connection.execute(
            insert(audit),
            [
                {
                    "id": f"page_audit_{i:03}",
                    "channel_id": "system",
                    "environment": "control",
                    "data_scope_id": None,
                    "subject_type": None,
                    "subject_id": None,
                    "actor_id": env.user_id,
                    "action": "account:update",
                    "target_type": "account",
                    "target_id": f"page_user_{i:03}",
                    "request_id": "page-request",
                    "outcome": "SUCCEEDED",
                    "summary": {"changed_fields": ["display_name"]},
                    "created_at": now + timedelta(seconds=i),
                    "updated_at": now,
                    "revision": 1,
                }
                for i in range(205)
            ],
        )
    first = await env.iam.accounts.page(env.admin, 200, 0, "分页账号")
    last = await env.iam.accounts.page(env.admin, 200, 200, "分页账号")
    assert first.total == last.total == 205 and len(last.items) == 5
    assert len({r.user_id for r in [*first.items, *last.items]}) == 205
    assert (await env.iam.accounts.page(env.admin, search="分页账号204")).items[
        0
    ].login_name == "page-204"
    first_channels = await env.services.channels.list_page(env.admin, 200, 0, "分页渠道")
    last_channels = await env.services.channels.list_page(env.admin, 200, 200, "分页渠道")
    assert first_channels.total == last_channels.total == 205 and len(last_channels.items) == 5
    events = await env.iam.audit.page(env.admin, AuditFilter(limit=200, request_id="page-request"))
    tail = await env.iam.audit.page(env.admin, AuditFilter(offset=200, request_id="page-request"))
    assert events.total == tail.total == 205 and len(tail.items) == 5
    assert len({r.event_id for r in [*events.items, *tail.items]}) == 205
    assert tail.items[-1].target_name == "分页账号000"
    assert all(
        r.actor_name == "平台管理员" and r.changed_fields == ["显示名称"] for r in tail.items
    )


async def test_channel_role_can_include_audit_but_not_platform_actions(channel_env):
    env = channel_env
    tenant = await provision(env)
    service = CustomRoles(env.iam.access)
    role = await service.save(
        tenant.manager, RoleSave(name="审计观察", allowed_actions=["audit:read"])
    )
    assert role["action_names"] == ["查看操作审计"]
    for action in ("account:manage", "menu:manage", "usage:platform"):
        with pytest.raises(ServiceError) as rejected:
            await service.save(tenant.manager, RoleSave(name="越权角色", allowed_actions=[action]))
        assert rejected.value.code == "ROLE_ACTIONS_INVALID"


async def test_role_editor_can_revoke_own_role_without_false_failure(channel_env):
    env = channel_env
    roles = CustomRoles(env.iam.access)
    role = await roles.save(env.admin, RoleSave(name="角色维护员", allowed_actions=["role:grant"]))
    _, _, session = await signed_account(env, "self-revoke", [role["id"]])
    saved = await roles.save(
        session,
        RoleSave(
            name="角色维护员",
            allowed_actions=["role:grant"],
            active=False,
            revision=role["revision"],
        ),
        role["id"],
    )
    assert not saved["active"] and saved["revision"] == role["revision"] + 1
    with pytest.raises(ServiceError) as denied:
        await roles.list(session)
    assert denied.value.status == 403
