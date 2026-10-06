"""成员行操作按实际写入边界显示，批量成员不增加逐行查询。"""

from collections import Counter

import pytest

from creativity_service.core.primitives import ServiceError
from creativity_service.modules.channels.repositories import management_scope_id
from creativity_service.modules.channels.schemas import ChannelCreate, EnvironmentCreate
from creativity_service.modules.iam.schemas import (
    AccountCreate,
    ChannelContextInput,
    MembershipInput,
)

from .conftest import INITIAL, login, provision
from .test_grant_actions import statements, stored_grant

pytestmark = pytest.mark.integration


def availability(row):
    return {action.action_key: action.enabled for action in row.actions}


async def add_member(env, tenant, index=0):
    account = await env.iam.accounts.create(
        env.admin,
        AccountCreate(
            login_name=f"member-actions-{index}",
            display_name=f"开发成员{index}",
            initial_password=INITIAL,
        ),
    )
    body = MembershipInput(
        roles=["builder"], environments=["test"], data_scopes=[tenant.domain.data_scope_id]
    )
    member = await env.iam.access.put_member(
        tenant.manager, tenant.channel.channel_id, account.user_id, body
    )
    return member, body


async def test_member_actions_distinguish_edit_and_remove_and_recheck_writes(channel_env):
    env = channel_env
    tenant = await provision(env)
    access, channel_id = env.iam.access, tenant.channel.channel_id
    member, body = await add_member(env, tenant)
    assert availability(member) == {"member:edit": True, "member:remove": True}
    edited = await access.put_member(
        tenant.manager,
        channel_id,
        member.user_id,
        body.model_copy(update={"revision": member.revision}),
    )
    assert availability(edited) == {"member:edit": True, "member:remove": True}
    # 其他授权人留下的独立发布权限阻止编辑激活潜在授权，但不阻止收回成员身份。
    await stored_grant(
        env,
        tenant,
        "latent_member_grant",
        grantee_type="account",
        grantee_id=member.user_id,
        resource_type="version",
        resource_id="*",
        allowed_actions=["release:publish"],
        environments=["test"],
        data_scopes=[tenant.domain.data_scope_id],
    )
    listed = {row.user_id: row for row in await access.list_members(tenant.manager, channel_id)}
    assert availability(listed[member.user_id]) == {"member:edit": False, "member:remove": True}
    assert listed[member.user_id].actions[0].disabled_reason == "不能超出本人的可授权范围"
    with pytest.raises(ServiceError, match="不能超出本人的可授权范围"):
        await access.put_member(
            tenant.manager,
            channel_id,
            member.user_id,
            body.model_copy(update={"revision": edited.revision}),
        )
    await access.remove_member(tenant.manager, channel_id, member.user_id, edited.revision)
    removed = next(
        row
        for row in await access.list_members(tenant.manager, channel_id)
        if row.user_id == member.user_id
    )
    assert removed.status == "DISABLED"
    # 操作者的权限收回后，已显示可操作的旧页面也不能继续写入。
    initial = next(
        row
        for row in await access.list_grants(tenant.manager, channel_id)
        if row.grant_id.startswith("initial_")
    )
    await stored_grant(
        env,
        tenant,
        initial.grant_id,
        revision=initial.revision,
        allowed_actions=["membership:read"],
    )
    readonly = await access.list_members(tenant.manager, channel_id)
    assert all(
        availability(row) == {"member:edit": False, "member:remove": False} for row in readonly
    )
    assert all(
        action.disabled_reason == "没有渠道成员管理权限"
        for row in readonly
        for action in row.actions
    )
    with pytest.raises(ServiceError) as denied:
        await access.remove_member(tenant.manager, channel_id, member.user_id, removed.revision)
    assert denied.value.status == 403


async def test_management_member_buttons_disabled_when_role_exceeds_delegation(channel_env):
    env = channel_env
    channel = await env.services.channels.create(
        env.admin,
        ChannelCreate(name="成员操作渠道", owner="负责人", first_admin_user_id=env.user_id),
    )
    await env.services.channels.create_environment(
        env.admin, channel.channel_id, EnvironmentCreate(environment="test", name="测试")
    )
    _, session = await login(env)
    token = await env.iam.sessions.enter(
        session,
        ChannelContextInput(
            channel_id=channel.channel_id,
            environment="test",
            data_scope_id=management_scope_id(channel.channel_id, "test"),
        ),
    )
    path = f"/admin/v1/channels/{channel.channel_id}/members"
    headers = {"Authorization": "Bearer " + token.access_token}
    response = await env.client.get(path, headers=headers)
    assert response.status_code == 200
    member = next(row for row in response.json() if row["user_id"] == env.user_id)
    assert {action["action_key"]: action["enabled"] for action in member["actions"]} == {
        "member:edit": False,
        "member:remove": False,
    }
    assert all(action["disabled_reason"] for action in member["actions"])
    body = {key: member[key] for key in MembershipInput.model_fields}
    assert (
        await env.client.put(path + "/" + env.user_id, headers=headers, json=body)
    ).status_code == 403
    assert (
        await env.client.delete(
            path + f"/{env.user_id}?revision={member['revision']}", headers=headers
        )
    ).status_code == 403


async def test_member_list_reuses_policy_and_catalog_without_n_plus_one(channel_env, monkeypatch):
    env = channel_env
    tenant = await provision(env)
    access, channel_id = env.iam.access, tenant.channel.channel_id

    async def forbidden_directory(*args, **kwargs):
        pytest.fail("成员列表不能复用全渠道目录查询流程")

    async def measured_list():
        with monkeypatch.context() as patch:
            patch.setattr(access.directory, "list_for", forbidden_directory)
            patch.setattr(access.directory, "data_for", forbidden_directory)
            with statements(env.engine) as queries:
                members = await access.list_members(tenant.manager, channel_id)
        return members, queries

    await add_member(env, tenant)
    before, single = await measured_list()
    for index in range(1, 11):
        await add_member(env, tenant, index)
    after, multiple = await measured_list()
    assert len(after) == len(before) + 10
    assert len(multiple) == len(single) <= 25
    counts = Counter(
        table
        for statement in multiple
        for table in ("channel_memberships", "resource_grants")
        if f"FROM {table}" in statement
    )
    # 一次读取操作者，一次读取成员列表；授权策略只读一次。
    assert counts == {"channel_memberships": 2, "resource_grants": 1}
    assert all(
        availability(row)
        == {"member:edit": row.user_id != env.user_id, "member:remove": row.user_id != env.user_id}
        for row in after
    )
    await env.iam.sessions.logout(tenant.manager)
    with pytest.raises(ServiceError) as denied:
        await access.list_members(tenant.manager, channel_id)
    assert denied.value.status == 401
