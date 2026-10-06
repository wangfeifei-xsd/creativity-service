"""数据域只取自已启用的专用 MCP 目录，不采信浏览器填写的名称与编号。"""

import pytest

from creativity_service.core.primitives import ServiceError, new_id
from creativity_service.core.security.outbound import Destination, OutboundPolicy
from creativity_service.modules.channels.repositories import management_scope_id
from creativity_service.modules.channels.schemas import (
    ChannelCreate,
    DataScopeFromSource,
    EnvironmentCreate,
)
from creativity_service.modules.iam.schemas import ChannelContextInput
from creativity_service.modules.mcp.assembly import build_mcp_service
from creativity_service.modules.mcp.schemas import McpCreate
from creativity_service.modules.tools.assembly import build_tool_services
from tests.integration.channels.conftest import login
from tests.integration.mcp.conftest import Keys

pytestmark = pytest.mark.integration


async def directory_ready(env):
    env.source.directory = True
    connection = await env.mcp.create(
        env.context, McpCreate(name="俱乐部目录", endpoint=env.source.endpoint)
    )
    snapshot = await env.mcp.probe(env.context, connection.connection_id, True)
    assert any(tool.purpose == "data_scope_directory" for tool in snapshot.tools)
    detail = await env.mcp.detail(env.context, connection.connection_id)
    await env.mcp.set_enabled(
        env.context, connection.connection_id, detail.connection.revision, True
    )
    return connection


async def test_directory_selection_uses_remote_name_type_and_number(mcp_env):
    env = mcp_env
    connection = await directory_ready(env)
    sources = await env.mcp.scope_sources(env.context)
    assert [(source.connection_id, source.remote_tool_name) for source in sources] == [
        (connection.connection_id, "list_data_scopes")
    ]
    directory = await env.mcp.scope_directory(
        env.context, connection.connection_id, "list_data_scopes"
    )
    assert [(item.name, item.type, item.id) for item in directory.items] == [
        ("俱乐部甲", "club", "club-001"),
        ("俱乐部乙", "club", "club-002"),
    ]
    before = len(env.source.calls)
    with pytest.raises(ServiceError) as error:
        await env.mcp.scope_directory(env.context, connection.connection_id, "lookup")
    assert error.value.code == "NOT_FOUND"
    assert len(env.source.calls) == before
    env.source.directory_items = [{"name": "无编号", "type": "club"}]
    with pytest.raises(ServiceError) as error:
        await env.mcp.scope_directory(env.context, connection.connection_id, "list_data_scopes")
    assert error.value.code == "MCP_RESULT_INVALID"


async def test_http_create_rechecks_source_and_rejects_manual_payload(mcp_env):
    env = mcp_env
    connection = await directory_ready(env)
    channel_id = env.context.scope.channel_id
    base = f"/admin/v1/channels/{channel_id}"
    source = {
        "environment": "test",
        "connection_id": connection.connection_id,
        "remote_tool_name": "list_data_scopes",
    }
    manual = await env.client.post(
        base + "/data-scopes",
        json={
            "environment": "test",
            "name": "自填",
            "external_scope_type": "club",
            "external_scope_id": "fake",
        },
    )
    assert manual.status_code == 410
    options = await env.client.get(base + "/data-scope-sources")
    assert options.status_code == 200
    assert options.json()[0]["remote_tool_name"] == "list_data_scopes"
    reading = await env.client.post(base + "/data-scope-directory", json=source)
    assert reading.status_code == 200
    assert reading.json()["items"][0]["name"] == "俱乐部甲"
    rejected = await env.client.post(
        base + "/data-scopes/from-source",
        json={
            **source,
            "external_scope_type": "club",
            "external_scope_id": "fake",
            "name": "伪造名称",
        },
    )
    assert rejected.status_code == 422
    env.source.directory_items = [{"name": "俱乐部甲", "type": "club", "id": "club-001"}]
    accepted = await env.client.post(
        base + "/data-scopes/from-source",
        json={**source, "external_scope_type": "club", "external_scope_id": "club-001"},
    )
    assert accepted.status_code == 201, accepted.text
    assert accepted.json()["name"] == "俱乐部甲"
    env.source.directory_items = []
    stale = await env.client.post(
        base + "/data-scopes/from-source",
        json={**source, "external_scope_type": "club", "external_scope_id": "club-002"},
    )
    assert stale.status_code == 404


async def test_directory_rejects_stale_and_disabled_source(mcp_env):
    env = mcp_env
    connection = await directory_ready(env)
    detail = await env.mcp.detail(env.context, connection.connection_id)
    await env.mcp.set_enabled(
        env.context, connection.connection_id, detail.connection.revision, False
    )
    assert await env.mcp.scope_sources(env.context) == []
    with pytest.raises(ServiceError) as error:
        await env.mcp.scope_directory(env.context, connection.connection_id, "list_data_scopes")
    assert error.value.code == "MCP_TEST_REQUIRED"


async def test_source_change_between_remote_read_and_write_rolls_back(mcp_env, monkeypatch):
    env = mcp_env
    connection = await directory_ready(env)
    original = env.mcp.scope_directory

    async def changed(context, connection_id, tool_name):
        result = await original(context, connection_id, tool_name)
        detail = await env.mcp.detail(context, connection_id)
        await env.mcp.set_enabled(context, connection_id, detail.connection.revision, False)
        return result

    monkeypatch.setattr(env.mcp, "scope_directory", changed)
    manager = await env.iam.authentication.admin_session(
        env.client.headers["Authorization"].removeprefix("Bearer "),
        "request_source_change",
        governance=True,
    )
    with pytest.raises(ServiceError) as error:
        await env.services.channels.create_data_scope_from_source(
            manager,
            env.context.scope.channel_id,
            DataScopeFromSource(
                environment="test",
                connection_id=connection.connection_id,
                remote_tool_name="list_data_scopes",
                external_scope_type="club",
                external_scope_id="club-001",
            ),
            env.mcp,
        )
    assert error.value.code == "MCP_TOOL_CHANGED"
    assert all(
        item.external_scope_id != "club-001"
        for item in await env.services.channels.data_scopes(env.admin, env.context.scope.channel_id)
    )


async def test_first_real_scope_can_be_selected_from_directory_in_management_workspace(
    channel_env,
    mcp_source,
):
    env = channel_env
    mcp_source.directory = True
    channel = await env.services.channels.create(
        env.admin,
        ChannelCreate(name="目录接入渠道", owner="负责人", first_admin_user_id=env.user_id),
    )
    await env.services.channels.create_environment(
        env.admin, channel.channel_id, EnvironmentCreate(environment="test", name="测试")
    )
    _, login_session = await login(env)
    token = await env.iam.sessions.enter(
        login_session,
        ChannelContextInput(
            channel_id=channel.channel_id,
            environment="test",
            data_scope_id=management_scope_id(channel.channel_id, "test"),
        ),
    )
    context = await env.iam.authentication.authenticate(token.access_token, "management")
    manager = await env.iam.authentication.admin_session(
        token.access_token, new_id("request"), governance=True
    )
    tools = build_tool_services(
        env.engine, env.iam.authorization, redis=env.redis, prefix=env.schema
    )
    directory = build_mcp_service(
        env.engine,
        env.iam.authorization,
        tools,
        key_provider=Keys(),
        outbound=OutboundPolicy(
            (
                Destination(
                    channel.channel_id,
                    "test",
                    "mcp",
                    "127.0.0.1",
                    port=mcp_source.port,
                    scheme="http",
                    allowed_networks=("127.0.0.1/32",),
                ),
            )
        ),
    )
    connection = await directory.create(
        context, McpCreate(name="俱乐部范围目录", endpoint=mcp_source.endpoint)
    )
    await directory.probe(context, connection.connection_id, True)
    detail = await directory.detail(context, connection.connection_id)
    await directory.set_enabled(context, connection.connection_id, detail.connection.revision, True)
    created = await env.services.channels.create_data_scope_from_source(
        manager,
        channel.channel_id,
        DataScopeFromSource(
            environment="test",
            connection_id=connection.connection_id,
            remote_tool_name="list_data_scopes",
            external_scope_type="club",
            external_scope_id="club-001",
        ),
        directory,
    )
    assert created.name == "俱乐部甲"
    assert created.external_scope_type == "club"
    assert created.external_scope_id == "club-001"
