"""公网 HTTPS 可直接接入，内网许可、请求头与其他用途保持隔离。"""

from unittest.mock import AsyncMock

import pytest

from creativity_service.core.context import Scope
from creativity_service.core.primitives import ServiceError
from creativity_service.core.security.outbound import Destination, OutboundPolicy

SCOPE = Scope(channel_id="rental", environment="dev")
PUBLIC_ADDRESSES = ["93.184.216.34", "2606:4700:4700::1111"]


def policy(resolver, destinations=()):
    return OutboundPolicy(destinations, resolver, public_https_purposes=frozenset({"mcp", "oauth"}))


@pytest.mark.parametrize("purpose", ["mcp", "oauth"])
async def test_public_https_without_registration_is_pinned_and_rechecked(purpose):
    resolver = AsyncMock(side_effect=[PUBLIC_ADDRESSES, ["127.0.0.1"]])
    outbound = policy(resolver)
    url = "https://rental.example:8443/api/mcp"
    target = await outbound.validate(SCOPE, purpose, url, {"Accept": "application/json"})
    assert target.url == url and target.addresses == tuple(PUBLIC_ADDRESSES)
    assert target.hostname == "rental.example" and target.port == 8443
    assert target.headers == {"accept": "application/json"}
    with pytest.raises(ServiceError) as error:
        await outbound.validate(SCOPE, purpose, url)
    assert error.value.code == "DESTINATION_FORBIDDEN"
    assert resolver.await_count == 2


@pytest.mark.parametrize("purpose", ["mcp", "oauth"])
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
async def test_one_nonpublic_dns_answer_blocks_public_https(purpose, address):
    outbound = policy(AsyncMock(return_value=[*PUBLIC_ADDRESSES, address]))
    with pytest.raises(ServiceError) as error:
        await outbound.validate(SCOPE, purpose, "https://rental.example/api/mcp")
    assert error.value.code == "DESTINATION_FORBIDDEN"


@pytest.mark.parametrize(
    "purpose,url,headers",
    [
        ("mcp", "http://rental.example/mcp", {}),
        ("oauth", "http://rental.example/token", {}),
        ("http_tool", "https://rental.example/mcp", {}),
        ("identity", "https://rental.example/mcp", {}),
        ("webhook", "https://rental.example/mcp", {}),
        ("model", "https://rental.example/mcp", {}),
        ("mcp", "https://user:secret@rental.example/mcp", {}),
        ("mcp", "https://rental.example/api/%2e%2e/admin", {}),
        ("oauth", "https://rental.example/token#fragment", {}),
        ("mcp", "https://rental.example/mcp", {"Authorization": "secret"}),
        ("oauth", "https://rental.example/token", {"Host": "internal"}),
        ("mcp", "https://rental.example/mcp", {"X-Unregistered": "value"}),
        ("oauth", "https://rental.example/token", {"Accept": "x\r\nAuthorization: secret"}),
    ],
)
async def test_public_https_keeps_scheme_purpose_url_and_header_guards(purpose, url, headers):
    resolver = AsyncMock(return_value=PUBLIC_ADDRESSES)
    with pytest.raises(ServiceError) as error:
        await policy(resolver).validate(SCOPE, purpose, url, headers)
    assert error.value.code == "DESTINATION_FORBIDDEN"
    resolver.assert_not_awaited()


async def test_explicit_private_permission_remains_scoped_to_channel_environment_and_purpose():
    rule = Destination(
        SCOPE.channel_id,
        SCOPE.environment,
        "mcp",
        "internal.example",
        allowed_networks=("10.0.0.1/32",),
        path_prefix="/mcp",
    )
    outbound = policy(AsyncMock(return_value=["10.0.0.1"]), (rule,))
    url = "https://internal.example/mcp"
    assert (await outbound.validate(SCOPE, "mcp", url)).addresses == ("10.0.0.1",)
    for scope, purpose, endpoint in [
        (SCOPE.model_copy(update={"channel_id": "other"}), "mcp", url),
        (SCOPE.model_copy(update={"environment": "prod"}), "mcp", url),
        (SCOPE, "oauth", url),
        (SCOPE, "mcp", "https://internal.example/admin"),
    ]:
        with pytest.raises(ServiceError) as error:
            await outbound.validate(scope, purpose, endpoint)
        assert error.value.code == "DESTINATION_FORBIDDEN"


async def test_public_https_cannot_bypass_matching_explicit_restrictions():
    rule = Destination(
        SCOPE.channel_id,
        SCOPE.environment,
        "mcp",
        "rental.example",
        allowed_networks=("93.184.216.34/32",),
        allowed_headers=frozenset(),
    )
    url = "https://rental.example/mcp"
    for rules, addresses, headers in [
        ((rule,), PUBLIC_ADDRESSES, {}),
        ((rule,), ["93.184.216.34"], {"Accept": "application/json"}),
        ((rule, rule), ["93.184.216.34"], {}),
    ]:
        with pytest.raises(ServiceError) as error:
            await policy(AsyncMock(return_value=addresses), rules).validate(
                SCOPE, "mcp", url, headers
            )
        assert error.value.code == "DESTINATION_FORBIDDEN"
