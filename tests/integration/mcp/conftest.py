"""复用真实 IAM、MySQL、Redis 与独立 MCP HTTP 服务。"""

import pytest

from creativity_service.core.security.outbound import OutboundPolicy
from creativity_service.modules.mcp.assembly import build_mcp_service
from tests.integration.tools.conftest import channel_env, tools_env
from tests.mcp_fixture import mcp_source

__all__ = ["channel_env", "tools_env", "mcp_source"]


@pytest.fixture
async def mcp_env(tools_env, mcp_source):
    env = tools_env
    env.source = mcp_source
    env.mcp = build_mcp_service(
        env.engine,
        env.iam.authorization,
        env.tools,
        outbound=OutboundPolicy(()),
    )
    env.client._transport.app.state.mcp = env.mcp
    return env
