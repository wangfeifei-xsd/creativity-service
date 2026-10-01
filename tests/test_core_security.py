"""出站地址、重定向和请求头必须按范围明确登记。"""

import pytest

from creativity_service.core.context import Scope
from creativity_service.core.primitives import ServiceError
from creativity_service.core.security.outbound import Destination, OutboundPolicy


async def public_resolver(host, port):
    return ["93.184.216.34"]


async def test_outbound_requires_scope_purpose_dns_and_safe_headers():
    scope = Scope(channel_id="first", environment="prod")
    rule = Destination("first", "prod", "model", "api.example.com", path_prefix="/v1")
    policy = OutboundPolicy((rule,), public_resolver)
    target = await policy.validate(
        scope, "model", "https://api.example.com/v1/messages", {"Accept": "application/json"}
    )
    assert target.addresses == ("93.184.216.34",)
    for purpose, url, headers in [
        ("mcp", target.url, {}),
        ("model", "https://other.example.com/v1/messages", {}),
        ("model", "https://api.example.com/v1/../admin", {}),
        ("model", target.url, {"Authorization": "secret"}),
        ("model", target.url, {"Accept": "x\r\nAuthorization: secret"}),
    ]:
        with pytest.raises(ServiceError):
            await policy.validate(scope, purpose, url, headers)
    with pytest.raises(ServiceError):
        await policy.redirect(scope, "model", target, "https://untrusted.example.com/v1", 1)

    async def rebound(host, port):
        return ["127.0.0.1"]

    with pytest.raises(ServiceError):
        await OutboundPolicy((rule,), rebound).validate(scope, "model", target.url)
    private = Destination(
        "first", "prod", "model", "api.example.com", allowed_networks=("127.0.0.1/32",)
    )
    assert (
        await OutboundPolicy((private,), rebound).validate(scope, "model", target.url)
    ).addresses == ("127.0.0.1",)
