"""服务鉴权的隔离、续期、并发合并与失败不重放验证。"""

import asyncio
import json
from time import monotonic
from unittest.mock import AsyncMock
from urllib.parse import parse_qs

import pytest
from pydantic import SecretBytes

from creativity_service.core.primitives import ServiceError
from creativity_service.core.security.outbound import Destination, OutboundPolicy
from creativity_service.integrations.outbound import HttpResponse
from creativity_service.modules.mcp.client_credentials import ClientCredentials
from tests.test_tools import context


def connection():
    return {
        "id": "mcp_a",
        "configuration_revision": 1,
        "credential_ref": "secret_a",
        "credential_revision": 1,
        "endpoint": "https://rental.example/mcp",
        "timeouts": {"operation_seconds": 30},
        "authentication": {
            "mode": "client_credentials",
            "app_id": "app_a",
            "token_endpoint": "https://rental.example/mcp/token",
        },
    }


def response(**changes):
    return HttpResponse(
        200,
        json.dumps(
            {
                "access_token": "short-lived-token",
                "expires_in": 900,
                "token_type": "Bearer",
                "scope": "mcp",
                **changes,
            }
        ).encode(),
    )


async def test_exchange_is_shared_only_inside_same_binding_and_renews():
    http = AsyncMock()
    http.post.return_value = response()
    auth = ClientCredentials(OutboundPolicy(()), http)
    operation = AsyncMock(return_value="ok")
    row, ctx, secret = connection(), context(), SecretBytes(b"private-app-secret")
    assert (
        await asyncio.gather(*(auth.call(ctx, row, secret, operation) for _ in range(10)))
        == ["ok"] * 10
    )
    assert http.post.await_count == 1
    params = parse_qs(http.post.call_args.args[3].decode())
    assert params == {
        "grant_type": ["client_credentials"],
        "client_id": ["app_a"],
        "client_secret": ["private-app-secret"],
        "resource": [row["endpoint"]],
        "scope": ["mcp"],
    }
    assert operation.call_args.args[0].get_secret_value() == b"short-lived-token"
    for other in [
        ctx.model_copy(update={"scope": ctx.scope.model_copy(update={"channel_id": "other"})}),
        ctx.model_copy(update={"scope": ctx.scope.model_copy(update={"environment": "dev"})}),
    ]:
        await auth.call(other, row, secret, operation)
    await auth.call(ctx, {**row, "configuration_revision": 2}, secret, operation)
    assert http.post.await_count == 4
    key = next(iter(auth.cache))
    auth.cache[key] = (SecretBytes(b"expiring-token"), monotonic() + 10)
    await auth.call(ctx, row, secret, operation)
    assert http.post.await_count == 5


async def test_rejected_token_does_not_replay_operation_and_next_call_exchanges():
    http = AsyncMock()
    http.post.return_value = response()
    auth = ClientCredentials(OutboundPolicy(()), http)
    operation = AsyncMock(side_effect=ServiceError("MCP_AUTH_FAILED", "拒绝", 403))
    with pytest.raises(ServiceError, match="访问令牌已失效"):
        await auth.call(context(), connection(), SecretBytes(b"secret"), operation)
    assert operation.await_count == 1 and not auth.cache
    operation.side_effect = None
    await auth.call(context(), connection(), SecretBytes(b"secret"), operation)
    assert http.post.await_count == 2


@pytest.mark.parametrize(
    "value",
    [
        response(expires_in=0),
        response(expires_in=True),
        response(access_token="bad\nvalue"),
        response(token_type="Basic"),
        response(scope="admin"),
        HttpResponse(401, b'{"error":"secret-must-not-leak"}'),
    ],
)
async def test_invalid_credentials_never_reach_mcp(value):
    http, operation = AsyncMock(), AsyncMock()
    http.post.return_value = value
    with pytest.raises(ServiceError) as error:
        await ClientCredentials(OutboundPolicy(()), http).call(
            context(), connection(), SecretBytes(b"secret"), operation
        )
    assert "secret-must-not-leak" not in str(error.value)
    operation.assert_not_awaited()


async def test_token_destination_requires_separate_oauth_allowlist():
    ctx = context()
    policy = OutboundPolicy(
        (Destination(ctx.scope.channel_id, ctx.scope.environment, "mcp", "rental.example"),)
    )
    with pytest.raises(ServiceError) as error:
        await ClientCredentials(policy).call(ctx, connection(), SecretBytes(b"secret"), AsyncMock())
    assert error.value.code == "DESTINATION_FORBIDDEN"
