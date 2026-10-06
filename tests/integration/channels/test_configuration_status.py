"""环境提示只统计当前页；接入服务及 Key 不影响渠道管理。"""

import pytest
from sqlalchemy import event

from creativity_service.core.primitives import new_id
from creativity_service.modules.channels.schemas import (
    ChannelCreate,
    EnvironmentCreate,
    EnvironmentUpdate,
)
from creativity_service.modules.iam.custom_roles import CustomRoles, RoleSave
from creativity_service.modules.iam.schemas import (
    AccountCreate,
    ChannelContextInput,
    GrantInput,
    MembershipInput,
)
from tests.integration.channels.test_administrator_accounts import activate

from .conftest import INITIAL, credential, login, provision

pytestmark = pytest.mark.integration


def completed(view):
    return {item["key"]: item["completed"] for item in view["configuration_status"]}


async def listed(env, token):
    response = await env.client.get(
        "/admin/v1/channels/page", headers={"Authorization": "Bearer " + token.access_token}
    )
    assert response.status_code == 200, response.text
    return {item["channel_id"]: item for item in response.json()["items"]}


async def test_environment_hint_does_not_depend_on_service_or_key(channel_env):
    env = channel_env
    configured = await provision(env, "alpha")
    pending = await env.services.channels.create(
        env.admin,
        ChannelCreate(name="待配置渠道", owner="负责人", first_admin_user_id=env.user_id),
    )
    items = await listed(env, env.admin_token)
    assert completed(items[configured.channel.channel_id]) == {"environments": True}
    assert completed(items[pending.channel_id]) == {"environments": False}
    assert items[pending.channel_id]["configuration_status"][0]["path"] == (
        f"/channels/{pending.channel_id}?tab=environments"
    )
    assert items[pending.channel_id]["configuration_status"][0]["message"].startswith("未配置：")
    await env.services.channels.create_environment(
        env.admin, pending.channel_id, EnvironmentCreate(environment="dev", name="开发")
    )
    items = await listed(env, env.admin_token)
    assert completed(items[pending.channel_id]) == {"environments": True}
    await env.services.channels.update_environment(
        env.admin,
        configured.channel.channel_id,
        "test",
        EnvironmentUpdate(revision=1, status="DISABLED"),
    )
    items = await listed(env, env.admin_token)
    assert completed(items[configured.channel.channel_id]) == {"environments": False}
    assert completed(items[pending.channel_id]) == {"environments": True}


async def test_management_workspace_reports_environment_without_service_or_key(
    channel_env,
):
    env = channel_env
    configured = await env.services.channels.create(
        env.admin,
        ChannelCreate(name="空接入配置", owner="负责人", first_admin_user_id=env.user_id),
    )
    await env.services.channels.create_environment(
        env.admin, configured.channel_id, EnvironmentCreate(environment="test", name="测试")
    )
    _, session = await login(env)
    token = await env.iam.sessions.enter(
        session,
        ChannelContextInput(
            channel_id=configured.channel_id,
            environment="test",
        ),
    )
    items = await listed(env, token)
    assert completed(items[configured.channel_id]) == {"environments": True}


async def test_configuration_queries_are_constant_and_only_aggregate_current_page(channel_env):
    env = channel_env
    configured = await provision(env, "alpha")
    await env.services.channels.create(
        env.admin,
        ChannelCreate(name="待配置渠道", owner="负责人", first_admin_user_id=env.user_id),
    )
    queries = []

    def capture(connection, cursor, statement, parameters, context, executemany):
        if statement.lstrip().startswith("WITH configured_"):
            queries.append(statement)

    event.listen(env.engine.sync_engine, "before_cursor_execute", capture)
    try:
        first = await env.services.channels.list_page(env.admin, limit=1)
        assert len(first.items) == 1 and first.items[0].channel_id == configured.channel.channel_id
        assert len(queries) == 1
        queries.clear()
        both = await env.services.channels.list_page(env.admin)
        assert len(both.items) == 2 and len(queries) == 1
        queries.clear()
        tail = await env.services.channels.list_page(env.admin, limit=1, offset=1)
        assert len(tail.items) == 1 and len(queries) == 1
        assert not any(item.completed for item in tail.items[0].configuration_status)
        queries.clear()
        empty = await env.services.channels.list_page(env.admin, offset=2)
        assert empty.items == [] and queries == []
    finally:
        event.remove(env.engine.sync_engine, "before_cursor_execute", capture)


async def test_configuration_status_does_not_expose_other_channels_or_environments(channel_env):
    env = channel_env
    own = await provision(env, "alpha")
    foreign = await provision(env, "beta")
    await credential(env, foreign)
    await env.services.channels.create_environment(
        env.admin, own.channel.channel_id, EnvironmentCreate(environment="prod", name="生产")
    )
    platform = await listed(env, env.admin_token)
    assert platform[own.channel.channel_id]["configuration_status"][0]["message"].startswith(
        "已配置：2 "
    )
    _, session = await login(env)
    token = await env.iam.sessions.enter(
        session,
        ChannelContextInput(
            channel_id=own.channel.channel_id,
            environment="test",
        ),
    )
    current = await listed(env, token)
    assert list(current) == [own.channel.channel_id]
    assert current[own.channel.channel_id]["configuration_status"][0]["message"].startswith(
        "已配置：1 "
    )
    assert completed(current[own.channel.channel_id]) == {
        "environments": True,
    }
    manager = await env.iam.authentication.admin_session(token.access_token, new_id("request"))
    role = await CustomRoles(env.iam.access).save(
        manager,
        RoleSave(
            name="环境配置员",
            grant_scope="channel",
            allowed_actions=["channel:manage", "environment:manage"],
        ),
    )
    account = await env.iam.accounts.create(
        env.admin,
        AccountCreate(
            login_name="configuration-reader", display_name="配置员", initial_password=INITIAL
        ),
    )
    await env.iam.access.put_member(
        manager,
        own.channel.channel_id,
        account.user_id,
        MembershipInput(roles=[role["id"]], environments=["test"]),
    )
    await env.iam.access.put_grant(
        manager,
        own.channel.channel_id,
        "configuration-reader",
        GrantInput(
            grantee_type="account",
            grantee_id=account.user_id,
            resource_type="channel",
            resource_id=own.channel.channel_id,
            allowed_actions=["channel:manage", "environment:manage"],
            environments=["test"],
        ),
    )
    _, session = await activate(env, account)
    restricted = await env.iam.sessions.enter(
        session,
        ChannelContextInput(
            channel_id=own.channel.channel_id,
            environment="test",
        ),
    )
    limited = await listed(env, restricted)
    assert completed(limited[own.channel.channel_id]) == {"environments": True}
