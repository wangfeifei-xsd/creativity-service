"""复用真实 IAM、PostgreSQL、Redis 与独立 MCP HTTP 服务。"""

import pytest
from pydantic import SecretBytes

from creativity_service.core.security.outbound import Destination, OutboundPolicy
from creativity_service.modules.mcp.assembly import build_mcp_service
from tests.integration.tools.conftest import channel_env, tools_env
from tests.mcp_fixture import mcp_source

__all__ = ["channel_env", "tools_env", "mcp_source"]


class Keys:
    async def current(self):
        return "test-key", SecretBytes(b"m" * 32)

    async def resolve(self, version):
        return SecretBytes(b"m" * 32)


@pytest.fixture
async def mcp_env(tools_env, mcp_source):
    env = tools_env
    env.source = mcp_source
    env.mcp = build_mcp_service(
        env.engine,
        env.iam.authorization,
        env.tools,
        key_provider=Keys(),
        outbound=OutboundPolicy(
            (
                Destination(
                    env.context.scope.channel_id,
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
    env.client._transport.app.state.mcp = env.mcp
    return env
