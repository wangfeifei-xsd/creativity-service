"""HTTPS 配置不查询 DNS，所有用途的 HTTPS 请求不限制目的地或 IP 范围。"""

from unittest.mock import AsyncMock

import pytest

from creativity_service.core.context import Scope
from creativity_service.core.primitives import ServiceError
from creativity_service.core.security.outbound import Destination, OutboundPolicy

SCOPE = Scope(channel_id="rental", environment="dev")
PURPOSES = ["model", "mcp", "http_tool", "oauth", "webhook", "identity"]
PUBLIC_ADDRESSES = ["93.184.216.34", "2606:4700:4700::1111"]


@pytest.mark.parametrize("purpose", PURPOSES)
@pytest.mark.parametrize(
    "url",
    [
        "https://gadev.g-buddy.cn/mcp",
        "https://gadev.g-buddy.cn/mcp/token",
        "https://unresolvable.invalid:8443/api",
        "https://10.0.0.1/api",
        "https://198.18.0.253/api",
        "https://localhost/api",
        "https://[::1]/api",
    ],
)
async def test_https_configuration_saves_without_dns_or_allowlist(purpose, url):
    resolver = AsyncMock(side_effect=OSError("DNS 不可用"))
    await OutboundPolicy((), resolver).validate_configuration(SCOPE, purpose, url)
    resolver.assert_not_awaited()


@pytest.mark.parametrize("purpose", PURPOSES)
@pytest.mark.parametrize(
    "address",
    [
        "127.0.0.1",
        "10.0.0.1",
        "192.168.1.1",
        "169.254.169.254",
        "198.18.0.253",
        "::1",
        "fc00::1",
        "fe80::1",
    ],
)
async def test_https_requests_accept_all_dns_addresses_and_resolve_each_call(purpose, address):
    resolver = AsyncMock(side_effect=[PUBLIC_ADDRESSES, [*PUBLIC_ADDRESSES, address]])
    outbound = OutboundPolicy((), resolver)
    url = "https://rental.example:8443/api/mcp"
    first = await outbound.validate(SCOPE, purpose, url)
    target = await outbound.validate(SCOPE, purpose, url, {"Accept": "application/json"})
    assert first.addresses == tuple(PUBLIC_ADDRESSES)
    assert target.addresses == (*PUBLIC_ADDRESSES, address)
    assert target.url == url and target.hostname == "rental.example" and target.port == 8443
    assert target.headers == {"accept": "application/json"}
    assert resolver.await_count == 2


@pytest.mark.parametrize("purpose", PURPOSES)
async def test_https_ignores_existing_allowlist_restrictions(purpose):
    rule = Destination(
        SCOPE.channel_id,
        SCOPE.environment,
        purpose,
        "rental.example",
        allowed_networks=("93.184.216.34/32",),
        path_prefix="/mcp",
        allowed_headers=frozenset(),
    )
    for rules in [(rule,), (rule, rule)]:
        outbound = OutboundPolicy(rules, AsyncMock(return_value=["198.18.0.253"]))
        for path in ["/mcp", "/other"]:
            target = await outbound.validate(
                SCOPE, purpose, f"https://rental.example{path}", {"Accept": "application/json"}
            )
            assert target.addresses == ("198.18.0.253",)


@pytest.mark.parametrize("result", [[], OSError("DNS 不可用")])
async def test_https_dns_failure_is_only_reported_when_connecting(result):
    resolver = AsyncMock(
        side_effect=result if isinstance(result, OSError) else None,
        return_value=result,
    )
    outbound = OutboundPolicy((), resolver)
    url = "https://rental.example/mcp"
    await outbound.validate_configuration(SCOPE, "mcp", url)
    resolver.assert_not_awaited()
    with pytest.raises(ServiceError) as error:
        await outbound.validate(SCOPE, "mcp", url)
    assert error.value.code == "DESTINATION_UNAVAILABLE"


@pytest.mark.parametrize(
    "url,headers",
    [
        ("https://user:secret@rental.example/mcp", {}),
        ("https://rental.example/api/%2e%2e/admin", {}),
        ("https://rental.example/token#fragment", {}),
        ("https:///mcp", {}),
        ("https://rental.example:0/mcp", {}),
        ("https://rental.example:65536/mcp", {}),
        ("https://rental.example/mcp", {"Authorization": "secret"}),
        ("https://rental.example/token", {"Host": "internal"}),
        ("https://rental.example/mcp", {"X-Unregistered": "value"}),
        ("https://rental.example/token", {"Accept": "x\r\nAuthorization: secret"}),
    ],
)
async def test_url_format_and_transport_headers_remain_validated(url, headers):
    resolver = AsyncMock(return_value=PUBLIC_ADDRESSES)
    outbound = OutboundPolicy((), resolver)
    with pytest.raises(ServiceError):
        await outbound.validate(SCOPE, "mcp", url, headers)
    if not headers:
        with pytest.raises(ServiceError):
            await outbound.validate_configuration(SCOPE, "mcp", url)
    resolver.assert_not_awaited()
