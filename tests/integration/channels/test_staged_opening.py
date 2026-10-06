"""分步开通不伪造映射，首位管理员只在显式配置的真实范围内获得权限。"""

import asyncio

import pytest

from creativity_service.core.primitives import ServiceError, new_id
from creativity_service.modules.channels.presentation import page_view
from creativity_service.modules.channels.repositories import management_scope_id
from creativity_service.modules.channels.schemas import (
    ChannelCreate,
    DataScopeCreate,
    EnvironmentCreate,
)
from creativity_service.modules.iam.authorization import effective_actions
from creativity_service.modules.iam.repositories import one
from creativity_service.modules.iam.schemas import ChannelContextInput

from .conftest import login

pytestmark = pytest.mark.integration


async def staged_channel(env, **overrides):
    return await env.services.channels.create(
        env.admin,
        ChannelCreate(
            name="分步开通渠道",
            owner="负责人",
            first_admin_user_id=env.user_id,
            independent_actions=["run:approve", "data:export"],
            **overrides,
        ),
    )


async def test_staged_channel_no_fake_mapping_and_first_scope_activates_exact_grant(channel_env):
    env = channel_env
    channel = await staged_channel(env)
    channel_id = channel.channel_id
    assert channel.channel_code == "FBKT"
    assert await env.services.channels.environments(env.admin, channel_id) == []
    assert await env.services.channels.data_scopes(env.admin, channel_id) == []
    assert await env.iam.sessions.channels(env.admin) == []
    page = await page_view(env.services.channels, env.admin, channel_id)
    assert page.pending_administrator.value == env.user_id
    member = await env.iam.access.repository.membership(channel_id, env.user_id)
    assert member.roles == ["channel_admin"]
    assert member.environments == member.data_scopes == []
    grants = await env.iam.access.repository.grants(channel_id)
    assert effective_actions(member, grants, "test", "default", "channel", channel_id) == set()
    with pytest.raises(ServiceError):
        await env.iam.sessions.enter(
            env.admin,
            ChannelContextInput(channel_id=channel_id, environment="test", data_scope_id="default"),
        )
    await env.services.channels.create_environment(
        env.admin, channel_id, EnvironmentCreate(environment="test", name="测试")
    )
    manage_id = management_scope_id(channel_id, "test")
    assert [item.data_scope_id for item in await env.iam.sessions.channels(env.admin)] == [
        manage_id
    ]
    body = DataScopeCreate(
        name="研发资料",
        environment="test",
        external_scope_type="org/project",
        external_scope_id="001/甲",
        administrator_id=env.user_id,
    )
    for administrator in (None, "another_account"):
        with pytest.raises(ServiceError) as failure:
            await env.services.channels.create_data_scope(
                env.admin, channel_id, body.model_copy(update={"administrator_id": administrator})
            )
        assert failure.value.code == "INITIAL_ADMIN_REQUIRED"
    scope = await env.services.channels.create_data_scope(env.admin, channel_id, body)
    assert scope.external_scope_id == "001/甲"
    page = await page_view(env.services.channels, env.admin, channel_id)
    assert page.pending_administrator is None
    member = await env.iam.access.repository.membership(channel_id, env.user_id)
    grants = await env.iam.access.repository.grants(channel_id)
    assert member.environments == ["test"]
    assert set(member.data_scopes) == {manage_id, scope.data_scope_id}
    assert {"run:approve", "data:export"} <= effective_actions(
        member, grants, "test", scope.data_scope_id, "channel", channel_id
    )
    _, session = await login(env)
    token = await env.iam.sessions.enter(
        session,
        ChannelContextInput(
            channel_id=channel_id, environment="test", data_scope_id=scope.data_scope_id
        ),
    )
    await env.iam.authentication.admin_session(token.access_token, new_id("request"))
    other = await env.services.channels.create_data_scope(
        env.admin,
        channel_id,
        body.model_copy(update={"external_scope_id": "002/乙", "administrator_id": None}),
    )
    assert {
        item.data_scope_id for item in await env.iam.access.directory.list_for(env.user_id)
    } == {
        manage_id,
        scope.data_scope_id,
    }
    assert (
        effective_actions(member, grants, "test", other.data_scope_id, "channel", channel_id)
        == set()
    )


@pytest.mark.parametrize(
    "initial",
    [
        {"environment": "test"},
        {"data_scope": {"name": "资料", "external_scope_type": "org", "external_scope_id": "001"}},
    ],
)
async def test_partial_initial_configuration_is_rejected_without_writes(channel_env, initial):
    env = channel_env
    response = await env.client.post(
        "/admin/v1/channels",
        headers={"Authorization": f"Bearer {env.admin_token.access_token}"},
        json={
            "name": "分步开通渠道",
            "owner": "负责人",
            "first_admin_user_id": env.user_id,
            **initial,
        },
    )
    assert response.status_code == 422
    assert await env.services.channels.list_items(env.admin) == []


async def test_staged_first_member_failure_rolls_back_channel_and_code(channel_env, monkeypatch):
    env = channel_env

    async def failure(*args, **kwargs):
        raise RuntimeError("首位管理员保存失败")

    original = env.iam.access.provision_first_member
    monkeypatch.setattr(env.iam.access, "provision_first_member", failure)
    with pytest.raises(RuntimeError):
        await staged_channel(env)
    assert await env.services.channels.list_items(env.admin) == []
    monkeypatch.setattr(env.iam.access, "provision_first_member", original)
    assert (await staged_channel(env)).channel_code == "FBKT"


async def test_first_scope_grant_failure_rolls_back_mapping_and_pending_authorization(
    channel_env, monkeypatch
):
    env = channel_env
    channel = await staged_channel(env)
    await env.services.channels.create_environment(
        env.admin, channel.channel_id, EnvironmentCreate(environment="test", name="测试")
    )
    original = env.iam.access.provision_workspace

    async def failure(*args, **kwargs):
        await original(*args, **kwargs)
        raise RuntimeError("工作区授权提交失败")

    monkeypatch.setattr(env.iam.access, "provision_workspace", failure)
    body = DataScopeCreate(
        name="资料",
        environment="test",
        external_scope_type="org",
        external_scope_id="001",
        administrator_id=env.user_id,
    )
    with pytest.raises(RuntimeError):
        await env.services.channels.create_data_scope(env.admin, channel.channel_id, body)
    assert await env.services.channels.data_scopes(env.admin, channel.channel_id) == []
    member = await env.iam.access.repository.membership(channel.channel_id, env.user_id)
    assert member.environments == ["test"]
    assert member.data_scopes == [management_scope_id(channel.channel_id, "test")]
    monkeypatch.setattr(env.iam.access, "provision_workspace", original)
    await env.services.channels.create_data_scope(env.admin, channel.channel_id, body)


async def test_concurrent_first_scopes_preserve_initial_actions_in_only_one_scope(channel_env):
    env = channel_env
    channel = await staged_channel(env)
    await env.services.channels.create_environment(
        env.admin, channel.channel_id, EnvironmentCreate(environment="test", name="测试")
    )
    scopes = await asyncio.gather(
        *[
            env.services.channels.create_data_scope(
                env.admin,
                channel.channel_id,
                DataScopeCreate(
                    name=f"资料{i}",
                    environment="test",
                    external_scope_type="org",
                    external_scope_id=str(i),
                    administrator_id=env.user_id,
                ),
            )
            for i in range(2)
        ]
    )
    member = await env.iam.access.repository.membership(channel.channel_id, env.user_id)
    grants = await env.iam.access.repository.grants(channel.channel_id)
    assert set(member.data_scopes) == {
        management_scope_id(channel.channel_id, "test"),
        *(scope.data_scope_id for scope in scopes),
    }
    assert (
        sum(
            "run:approve"
            in effective_actions(
                member, grants, "test", scope.data_scope_id, "channel", channel.channel_id
            )
            for scope in scopes
        )
        == 1
    )
    async with env.engine.connect() as connection:
        initial = await one(
            connection, "resource_grants", channel.channel_id, id="initial_" + member.id
        )
        assert len(initial["data_scopes"]) == 2
        assert management_scope_id(channel.channel_id, "test") in initial["data_scopes"]
