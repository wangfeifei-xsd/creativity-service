"""平台维护渠道管理员身份，渠道内自建角色沿用现有授权规则。"""

import pytest

from creativity_service.core.context import AuthContext
from creativity_service.core.primitives import ServiceError, new_id
from creativity_service.modules.iam.custom_roles import CustomRoles, RoleSave
from creativity_service.modules.iam.presentation import access_options
from creativity_service.modules.iam.schemas import (
    AccountCreate,
    AccountUpdate,
    GrantInput,
    LoginInput,
    MembershipInput,
    PasswordChange,
)
from tests.support.captcha import captcha_token

from .conftest import INITIAL, PASSWORD, provision

pytestmark = pytest.mark.integration


async def test_platform_retains_admin_assignment_and_channel_hides_and_protects_it(channel_env):
    env = channel_env
    tenant = await provision(env)
    access, channel_id = env.iam.access, tenant.channel.channel_id
    roles = CustomRoles(access)
    assert "channel_admin" in {role["id"] for role in await roles.list(env.admin)}
    assert "channel_admin" in {
        role.role_code for role in await env.iam.accounts.role_options(env.admin)
    }
    assert "channel_admin" not in {role["id"] for role in await roles.list(tenant.manager)}
    assert "channel_admin" not in {role.role_code for role in await access.roles(tenant.manager)}
    options = await access_options(env.iam, tenant.manager, channel_id)
    assert "channel_admin" not in {role.role_code for role in options.roles}
    assert "channel_admin" not in {role.value for role in options.grantee_roles}
    member = next(
        row
        for row in await access.list_members(tenant.manager, channel_id)
        if row.user_id == env.user_id
    )
    assert member.role_names == ["渠道管理员"]
    assert all(not action.enabled and "平台" in action.disabled_reason for action in member.actions)
    target = await env.iam.accounts.create(
        env.admin,
        AccountCreate(
            login_name="platform-assigned-admin",
            display_name="平台分配的管理员",
            initial_password=INITIAL,
        ),
    )
    for user_id, codes, revision in (
        (target.user_id, ["channel_admin"], None),
        (env.user_id, ["builder"], member.revision),
    ):
        with pytest.raises(ServiceError) as denied:
            await access.put_member(
                tenant.manager,
                channel_id,
                user_id,
                MembershipInput(
                    revision=revision,
                    roles=codes,
                    environments=["test"],
                ),
            )
        assert denied.value.code == "PLATFORM_MANAGED_ROLE"
    with pytest.raises(ServiceError) as denied:
        await access.remove_member(tenant.manager, channel_id, env.user_id, member.revision)
    assert denied.value.code == "PLATFORM_MANAGED_ROLE"
    with pytest.raises(ServiceError) as denied:
        await access.put_grant(
            tenant.manager,
            channel_id,
            "blocked_admin_role",
            GrantInput(
                grantee_type="role",
                grantee_id="channel_admin",
                resource_type="version",
                resource_id="*",
                allowed_actions=["version:read"],
                environments=["test"],
            ),
        )
    assert denied.value.code == "PLATFORM_MANAGED_ROLE"
    assigned = await env.iam.accounts.update(
        env.admin,
        target.user_id,
        AccountUpdate(
            revision=target.revision,
            roles=["channel_admin"],
            channel_ids=[channel_id],
        ),
    )
    assert assigned.roles == ["channel_admin"] and assigned.channel_ids == [channel_id]
    removed = await env.iam.accounts.update(
        env.admin,
        target.user_id,
        AccountUpdate(
            revision=assigned.revision,
            roles=["channel_admin"],
            channel_ids=[],
        ),
    )
    assert removed.channel_ids == []
    # 角色定义仍参加真实鉴权，隐藏角色不能削减管理员本人的管理能力。
    allowed = await env.iam.authorization.allowed_actions(
        tenant.manager.context, "channel", channel_id
    )
    assert {"membership:manage", "grant:manage"} <= allowed


@pytest.mark.parametrize("action", ["usage:read", "membership:manage", "grant:manage"])
async def test_channel_created_roles_remain_assignable_without_new_action_restrictions(
    channel_env, action
):
    env = channel_env
    tenant = await provision(env)
    access, channel_id = env.iam.access, tenant.channel.channel_id
    roles = CustomRoles(access)
    local_options = await roles.options(tenant.manager)
    assert action in {item["value"] for item in local_options["actions"]}
    options = await access_options(env.iam, tenant.manager, channel_id)
    assert action in {item.action_key for item in options.grant_actions}
    role = await roles.save(tenant.manager, RoleSave(name="渠道自建角色", allowed_actions=[action]))
    target = await env.iam.accounts.create(
        env.admin,
        AccountCreate(
            login_name="channel-subordinate",
            display_name="渠道细分成员",
            initial_password=INITIAL,
        ),
    )
    issued = await env.iam.sessions.login(
        LoginInput(
            login_name="channel-subordinate",
            password=INITIAL,
            captcha_token=await captcha_token(env.iam, "channel-subordinate", "channel-test"),
        ),
        "channel-test",
        new_id("request"),
    )
    initial = await env.iam.authentication.admin_session(
        issued.access_token, new_id("request"), allow_initial=True
    )
    await env.iam.accounts.change_password(
        initial, PasswordChange(current_password=INITIAL, new_password=PASSWORD)
    )
    body = MembershipInput(roles=[role["id"]], environments=["test"])
    member = await access.put_member(tenant.manager, channel_id, target.user_id, body)
    assert all(item.enabled for item in member.actions)
    grant = await access.put_grant(
        tenant.manager,
        channel_id,
        "channel_role_grant",
        GrantInput(
            grantee_type="role",
            grantee_id=role["id"],
            resource_type="channel",
            resource_id=channel_id,
            allowed_actions=[action],
            environments=["test"],
        ),
    )
    assert all(item.enabled for item in grant.actions)
    context = AuthContext(
        scope=tenant.manager.context.scope,
        principal_type="worker",
        principal_id=target.user_id,
        actor_id=target.user_id,
        request_id=new_id("request"),
    )
    assert (await env.iam.authorization.check(context, action, "channel", channel_id)).allowed
    latest_options = await access_options(env.iam, tenant.manager, channel_id)
    assert role["id"] in {item.role_code for item in latest_options.roles}
    assert role["id"] in {item.value for item in latest_options.grantee_roles}
    await access.revoke_grant(tenant.manager, channel_id, grant.grant_id, grant.revision)
    await access.remove_member(tenant.manager, channel_id, target.user_id, member.revision)
