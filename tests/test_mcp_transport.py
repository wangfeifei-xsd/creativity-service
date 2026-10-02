"""MCP-A02/A04/A05/A06：真实协议分页、契约漂移和不重放边界。"""

import asyncio

import pytest

from creativity_service.core.context import Scope
from creativity_service.core.primitives import ServiceError
from creativity_service.core.security.outbound import Destination, OutboundPolicy
from creativity_service.modules.mcp.schemas import McpTimeouts
from creativity_service.modules.mcp.transport import McpTransport, SessionKey
from tests.mcp_fixture import mcp_source

__all__ = ["mcp_source"]


def transport(source):
    scopes = [Scope(channel_id=f"channel_{name}", environment="test") for name in ["a", "b"]]
    policy = OutboundPolicy(
        tuple(
            Destination(
                s.channel_id,
                "test",
                "mcp",
                "127.0.0.1",
                port=source.port,
                scheme="http",
                allowed_networks=("127.0.0.1/32",),
            )
            for s in scopes
        )
    )
    return McpTransport(policy), scopes


def key(scope, version=1):
    return SessionKey(
        scope.channel_id,
        scope.environment,
        "connection_a",
        version,
        f"credential_{version}",
        version,
    )


async def test_paginated_handshake_and_isolated_authentication(mcp_source):
    client, scopes = transport(mcp_source)
    results = await asyncio.gather(
        *(
            client.discover(s, key(s), mcp_source.endpoint, f"token-{s.channel_id}", McpTimeouts())
            for s in scopes
        )
    )
    assert all(r.handshake.protocolVersion == "2025-11-25" and len(r.tools) == 1 for r in results)
    assert len(mcp_source.sessions) == 2 and len(set(mcp_source.sessions.values())) == 2
    for _, auth, session in mcp_source.calls:
        if session:
            assert mcp_source.sessions[session] == auth
    await client.discover(
        scopes[0], key(scopes[0], 2), mcp_source.endpoint, "rotated", McpTimeouts()
    )
    assert len(mcp_source.sessions) == 3 and not client.active


@pytest.mark.parametrize(
    ("mode", "code"),
    [
        ("auth", "MCP_AUTH_FAILED"),
        ("redirect", "MCP_DESTINATION_FORBIDDEN"),
        ("cursor", "MCP_PROTOCOL_MISMATCH"),
        ("protocol", "MCP_PROTOCOL_MISMATCH"),
    ],
)
async def test_safe_protocol_failures(mcp_source, mode, code, caplog):
    client, scopes = transport(mcp_source)
    mcp_source.mode = mode
    with pytest.raises(ServiceError) as error:
        await client.discover(
            scopes[0],
            key(scopes[0]),
            mcp_source.endpoint,
            "private-token",
            McpTimeouts(operation_seconds=2),
        )
    assert error.value.code == code
    assert "private-token" not in str(error.value) and "private-token" not in caplog.text
    assert not client.active


async def test_changed_contract_prevents_call_and_disconnect_never_replays(mcp_source):
    client, scopes = transport(mcp_source)
    scope = scopes[0]
    discovered = await client.discover(scope, key(scope), mcp_source.endpoint, None, McpTimeouts())

    async def before():
        pass

    async def call():
        return await client.call(
            scope,
            key(scope),
            mcp_source.endpoint,
            None,
            McpTimeouts(operation_seconds=2),
            "lookup",
            discovered.tools[0].schema_hash,
            {"query": "test"},
            1024,
            before,
        )

    mcp_source.schema_revision = 2
    with pytest.raises(ServiceError, match="契约"):
        await call()
    assert not any(m["method"] == "tools/call" for m, _, _ in mcp_source.calls)
    mcp_source.schema_revision = 1
    mcp_source.mode = "disconnect"
    with pytest.raises(ServiceError):
        await call()
    assert sum(m["method"] == "tools/call" for m, _, _ in mcp_source.calls) == 1
    mcp_source.mode = "large"
    with pytest.raises(ServiceError) as error:
        await call()
    assert error.value.code == "MCP_RESULT_TOO_LARGE"


def test_title_and_description_changes_do_not_break_fixed_contract():
    from mcp.types import Tool, ToolAnnotations

    from creativity_service.core.primitives import utcnow
    from creativity_service.modules.mcp.differences import differences
    from creativity_service.modules.mcp.schemas import McpDiscovery
    from creativity_service.modules.mcp.transport import remote_tool

    original = Tool(
        name="lookup",
        description="目录",
        inputSchema={"type": "object"},
        annotations=ToolAnnotations(title="旧名称", readOnlyHint=True),
    )
    changed = original.model_copy(
        update={
            "description": "新描述",
            "annotations": ToolAnnotations(title="新名称", readOnlyHint=True),
        }
    )
    before = McpDiscovery(
        discovery_id="before",
        connection_revision=1,
        negotiated_version="2025-11-25",
        tools=[remote_tool(original)],
        discovered_at=utcnow(),
    )
    after = before.model_copy(update={"discovery_id": "after", "tools": [remote_tool(changed)]})
    diff = differences(after, before)
    assert diff.items[0].changes == ["description"] and not diff.items[0].breaking
