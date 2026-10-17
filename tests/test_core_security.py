"""出站重定向逐次解析，限制次数且不转发来源请求头。"""

from unittest.mock import AsyncMock

import pytest

from creativity_service.core.context import Scope
from creativity_service.core.primitives import ServiceError
from creativity_service.core.security.outbound import OutboundPolicy


@pytest.mark.parametrize("scheme", ["http", "https"])
async def test_outbound_redirect_resolves_target_and_drops_source_headers(scheme):
    scope = Scope(channel_id="first", environment="prod")
    resolver = AsyncMock(side_effect=[["127.0.0.1"], ["10.0.0.1"]])
    policy = OutboundPolicy((), resolver)
    target = await policy.validate(
        scope, "mcp", f"{scheme}://api.example.com/v1", {"X-Request-ID": "original"}
    )
    redirected = await policy.redirect(
        scope, "mcp", target, "http://other.example.com:32688/api/mcp", 1
    )
    assert redirected.hostname == "other.example.com" and redirected.port == 32688
    assert redirected.addresses == ("10.0.0.1",) and redirected.headers == {}
    assert target.headers == {"x-request-id": "original"}
    resolver.assert_awaited_with("other.example.com", 32688)
    for hop in [0, 4]:
        with pytest.raises(ServiceError) as error:
            await policy.redirect(scope, "mcp", target, "/other", hop)
        assert error.value.code == "REDIRECT_LIMIT"
    for url in ["file:///etc/hosts", "ftp://other.example.com/mcp"]:
        with pytest.raises(ServiceError) as error:
            await policy.redirect(scope, "mcp", target, url, 1)
        assert error.value.code == "DESTINATION_FORBIDDEN"
    assert resolver.await_count == 2
