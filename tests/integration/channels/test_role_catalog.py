"""角色管理、账号分配及运行时鉴权共享持久化定义。"""

import pytest

from creativity_service.core.context import ControlScope
from creativity_service.core.database import transaction
from creativity_service.core.locking import record_key
from creativity_service.core.primitives import ServiceError
from creativity_service.modules.iam.custom_roles import CustomRoles, RoleSave
from creativity_service.modules.iam.repositories import policy_key, rows, save
from creativity_service.modules.iam.schemas import (
    AccountCreate,
    AccountUpdate,
    ChannelContextInput,
    PasswordReset,
)
from tests.integration.channels.test_administrator_accounts import activate

from .conftest import INITIAL, provision

pytestmark = pytest.mark.integration


async def test_account_can_select_platform_and_multiple_channel_roles(channel_env):
    env = channel_env
    a = await provision(env, "alpha", None)
    b = await provision(env, "beta", None)
    roles = CustomRoles(env.iam.access)
    observer = await roles.save(
        env.admin, RoleSave(name="渠道观察员", allowed_actions=["run:read"], grant_scope="channel")
    )
    chosen = ["platform_admin", "channel_admin", observer["id"]]
    account = await env.iam.accounts.create(
        env.admin,
        AccountCreate(
            login_name="multiple-roles",
            display_name="多角色账号",
            initial_password=INITIAL,
            roles=chosen,
            channel_ids=[a.channel.channel_id, b.channel.channel_id],
        ),
    )
    assert account.roles == chosen
    assert account.role_names == ["平台管理员", "渠道管理员", "渠道观察员"]
    assert account.platform_roles == ["platform_admin"]
    assert account.role is None
    async with env.engine.connect() as connection:
        for channel in (a.channel, b.channel):
            member = (
                await rows(
                    connection, "channel_memberships", channel.channel_id, user_id=account.user_id
                )
            )[0]
            assert member["roles"] == ["channel_admin", observer["id"]]
    token, session = await activate(env, account)
    view = await env.iam.sessions.view(session)
    assert view.can_access_platform and view.default_workspace is None
    assert {option.channel_id for option in view.workspace_options} == set(account.channel_ids)
    assert (
        next(role for role in await roles.list(env.admin) if role["id"] == observer["id"])[
            "member_count"
        ]
        == 1
    )
    updated = await env.iam.accounts.update(
        env.admin,
        account.user_id,
        AccountUpdate(
            revision=(await env.iam.accounts.get(env.admin, account.user_id)).revision,
            roles=["platform_admin"],
            channel_ids=[],
        ),
    )
    assert updated.roles == ["platform_admin"] and not updated.channel_ids
    with pytest.raises(ServiceError) as revoked:
        await env.iam.authentication.admin_session(token.access_token, "revoked-multi-role")
    assert revoked.value.status == 401


async def test_multi_role_validation_rolls_back_and_protects_last_admin(channel_env):
    env = channel_env
    account = await env.iam.accounts.create(
        env.admin,
        AccountCreate(
            login_name="role-validation",
            display_name="校验账号",
            initial_password=INITIAL,
            roles=["channel_admin"],
        ),
    )
    for chosen in (["channel_admin", "channel_admin"], ["channel_admin", "missing-role"]):
        with pytest.raises(ServiceError) as unavailable:
            await env.iam.accounts.update(
                env.admin,
                account.user_id,
                AccountUpdate(revision=account.revision, roles=chosen),
            )
        assert unavailable.value.status == 422
        current = await env.iam.accounts.get(env.admin, account.user_id)
        assert current.revision == account.revision and current.roles == ["channel_admin"]
    admin = await env.iam.accounts.get(env.admin, env.user_id)
    with pytest.raises(ServiceError) as protected:
        await env.iam.accounts.update(
            env.admin,
            admin.user_id,
            AccountUpdate(revision=admin.revision, roles=["channel_admin"]),
        )
    assert protected.value.code == "LAST_PLATFORM_ADMIN"


async def test_builtin_roles_and_account_choices_read_the_same_database(channel_env):
    env = channel_env
    tenant = await provision(env)
    roles = CustomRoles(env.iam.access)
    catalog = {r["id"]: r for r in await roles.list(env.admin)}
    assert set(catalog) == {"platform_admin", "channel_admin"}
    assert catalog["platform_admin"]["builtin"] and catalog["channel_admin"]["builtin"]
    assert catalog["platform_admin"]["grant_scope"] == "platform"
    assert catalog["channel_admin"]["grant_scope"] == "channel"
    assert not catalog["channel_admin"]["editable"]
    options = {r.role_code: r for r in await env.iam.accounts.role_options(env.admin)}
    assert set(options) == {"platform_admin", "channel_admin"}
    for code, option in options.items():
        assert option.name == catalog[code]["name"]
        assert [a.action_key for a in option.actions] == catalog[code]["allowed_actions"]
    with pytest.raises(ServiceError) as readonly:
        await roles.save(
            env.admin,
            RoleSave(name="不可替换内置角色", allowed_actions=["run:read"], grant_scope="channel"),
            "channel_admin",
        )
    assert readonly.value.code == "BUILTIN_ROLE_READ_ONLY"
    with pytest.raises(ServiceError) as readonly:
        await roles.remove(env.admin, "channel_admin", catalog["channel_admin"]["revision"])
    assert readonly.value.code == "BUILTIN_ROLE_READ_ONLY"

    async with transaction(
        env.engine,
        ControlScope(purpose="roles", actor_id="deployment"),
        [policy_key("system"), record_key("system", "builtin_roles", "role_channel_admin")],
    ) as uow:
        row = (await rows(uow.connection, "builtin_roles", "system", role_code="channel_admin"))[0]
        await save(
            uow,
            "builtin_roles",
            row["id"],
            {"name": "渠道观察管理员", "allowed_actions": ["run:read"]},
            row["revision"],
        )
    options = {r.role_code: r for r in await env.iam.accounts.role_options(env.admin)}
    assert options["channel_admin"].name == "渠道观察管理员"
    assert [a.action_key for a in options["channel_admin"].actions] == ["run:read"]
    catalog = {r["id"]: r for r in await roles.list(env.admin)}
    assert catalog["channel_admin"]["name"] == options["channel_admin"].name
    actions = await env.iam.authorization.allowed_actions(
        tenant.manager.context, "channel", tenant.channel.channel_id
    )
    assert actions == {"run:read"}


async def test_custom_channel_role_assigns_multiple_channels_and_changes_live(channel_env):
    env = channel_env
    a, b, foreign = (
        await provision(env, "alpha", None),
        await provision(env, "beta", None),
        await provision(env, "foreign", None),
    )
    service = CustomRoles(env.iam.access)
    options = await service.options(env.admin, "channel")
    assert {s["value"] for s in options["scopes"]} == {"platform", "channel"}
    role = await service.save(
        env.admin,
        RoleSave(
            name="渠道观察员", grant_scope="channel", allowed_actions=["run:read", "usage:read"]
        ),
    )
    choices = {r.role_code: r for r in await env.iam.accounts.role_options(env.admin)}
    assert choices[role["id"]].name == "渠道观察员"
    pending = await env.iam.accounts.create(
        env.admin,
        AccountCreate(
            login_name="role-pending",
            display_name="未分配渠道观察员",
            initial_password=INITIAL,
            role=role["id"],
        ),
    )
    assert pending.role == role["id"] and pending.channel_ids == []
    assert (await env.iam.accounts.get(env.admin, pending.user_id)).role_name == "渠道观察员"
    account = await env.iam.accounts.create(
        env.admin,
        AccountCreate(
            login_name="role-observer",
            display_name="观察账号",
            initial_password=INITIAL,
            role=role["id"],
            channel_ids=[a.channel.channel_id, b.channel.channel_id],
        ),
    )
    assert account.role == role["id"] and account.grant_scope == "channel"
    assert account.platform_roles == []
    _, login = await activate(env, account)
    session = await env.iam.sessions.view(login)
    assert not session.can_access_platform
    assert {w.channel_id for w in session.workspace_options} == {
        a.channel.channel_id,
        b.channel.channel_id,
    }
    assert session.default_workspace is not None
    token = await env.iam.sessions.enter(
        login,
        ChannelContextInput(
            channel_id=a.channel.channel_id,
            environment="test",
            data_scope_id=a.domain.data_scope_id,
        ),
    )
    manager = await env.iam.authentication.admin_session(token.access_token, "role-catalog-test")
    actions = await env.iam.authorization.allowed_actions(
        manager.context, "channel", a.channel.channel_id
    )
    assert actions == {"run:read", "usage:read"}
    with pytest.raises(ServiceError):
        await env.iam.sessions.enter(
            manager,
            ChannelContextInput(
                channel_id=foreign.channel.channel_id,
                environment="test",
                data_scope_id=foreign.domain.data_scope_id,
            ),
        )
    with pytest.raises(ServiceError) as readonly:
        await service.save(
            a.manager,
            RoleSave(
                name="越权修改共享角色", allowed_actions=["run:read"], revision=role["revision"]
            ),
            role["id"],
        )
    assert readonly.value.code == "ROLE_READ_ONLY"
    with pytest.raises(ServiceError) as referenced:
        await service.remove(env.admin, role["id"], role["revision"])
    assert referenced.value.code == "ROLE_REFERENCED"
    usage_menu = next(m for m in options["menus"] if m["page_key"] == "usage")
    role = await service.save(
        env.admin,
        RoleSave(
            name="渠道用量观察员",
            grant_scope="channel",
            allowed_actions=["model:manage", "run:read", "usage:read"],
            menu_ids=[usage_menu["id"]],
            revision=role["revision"],
        ),
        role["id"],
    )
    assert [n.navigation_key for n in (await env.iam.sessions.view(manager)).navigation] == [
        "usage"
    ]
    assert "model:manage" in await env.iam.authorization.allowed_actions(
        manager.context, "channel", a.channel.channel_id
    )
    role = await service.save(
        env.admin,
        RoleSave(
            name=role["name"],
            grant_scope="channel",
            allowed_actions=["usage:read"],
            menu_ids=[usage_menu["id"]],
            revision=role["revision"],
        ),
        role["id"],
    )
    assert await env.iam.authorization.allowed_actions(
        manager.context, "channel", a.channel.channel_id
    ) == {"usage:read"}
    role = await service.save(
        env.admin,
        RoleSave(
            name=role["name"],
            grant_scope="channel",
            allowed_actions=["usage:read"],
            menu_ids=[usage_menu["id"]],
            active=False,
            revision=role["revision"],
        ),
        role["id"],
    )
    assert role["id"] not in {r.role_code for r in await env.iam.accounts.role_options(env.admin)}
    assert not await env.iam.authorization.allowed_actions(
        manager.context, "channel", a.channel.channel_id
    )
    with pytest.raises(ServiceError):
        await env.iam.accounts.update(
            env.admin,
            account.user_id,
            AccountUpdate(
                revision=account.revision, role=role["id"], channel_ids=[a.channel.channel_id]
            ),
        )
    assert set((await env.iam.accounts.get(env.admin, account.user_id)).channel_ids) == {
        a.channel.channel_id,
        b.channel.channel_id,
    }


async def test_account_assignment_validates_role_scope_and_delegation(channel_env):
    env = channel_env
    tenant = await provision(env)
    service = CustomRoles(env.iam.access)
    platform_role = await service.save(
        env.admin, RoleSave(name="账号操作员", allowed_actions=["account:manage", "role:grant"])
    )
    target = await env.iam.accounts.create(
        env.admin,
        AccountCreate(
            login_name="catalog-delegator",
            display_name="低权限操作员",
            initial_password=INITIAL,
            role=platform_role["id"],
        ),
    )
    assert target.role_name == "账号操作员" and target.platform_roles == [platform_role["id"]]
    _, actor = await activate(env, target)
    assert {r.role_code for r in await env.iam.accounts.role_options(actor)} == {
        platform_role["id"]
    }
    with pytest.raises(ServiceError) as forbidden:
        await env.iam.accounts.create(
            actor,
            AccountCreate(
                login_name="no-channel-govern",
                display_name="越权渠道账号",
                initial_password=INITIAL,
                role="channel_admin",
                channel_ids=[tenant.channel.channel_id],
            ),
        )
    assert forbidden.value.status == 403
    channel_admin = await env.iam.accounts.create(
        env.admin,
        AccountCreate(
            login_name="catalog-channel-admin",
            display_name="受保护的渠道账号",
            initial_password=INITIAL,
            role="channel_admin",
            channel_ids=[tenant.channel.channel_id],
        ),
    )
    with pytest.raises(ServiceError) as takeover:
        await env.iam.accounts.reset(
            actor,
            channel_admin.user_id,
            PasswordReset(revision=channel_admin.revision, initial_password=INITIAL),
        )
    assert takeover.value.status == 403
    local_role = await service.save(
        tenant.manager, RoleSave(name="本渠道角色", allowed_actions=["run:read"])
    )
    assert local_role["id"] not in {
        r.role_code for r in await env.iam.accounts.role_options(env.admin)
    }
    with pytest.raises(ServiceError):
        await env.iam.accounts.create(
            env.admin,
            AccountCreate(
                login_name="cross-local-role",
                display_name="错误的共享身份",
                initial_password=INITIAL,
                role=local_role["id"],
                channel_ids=[tenant.channel.channel_id],
            ),
        )
    for actions in (["account:manage"], ["release:publish"]):
        with pytest.raises(ServiceError):
            await service.save(
                env.admin,
                RoleSave(name="无效渠道权限", grant_scope="channel", allowed_actions=actions),
            )
