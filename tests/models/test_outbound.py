"""连接授权出站覆盖公网、内网、重解析及保留地址边界。"""

import pytest

from creativity_service.core.context import Scope
from creativity_service.core.database import assert_external_io_allowed
from creativity_service.core.primitives import ServiceError
from creativity_service.core.security.outbound import OutboundPolicy
from creativity_service.modules.models import outbound


@pytest.fixture
def resolution(monkeypatch):
    addresses = ["93.184.216.34"]
    calls = []

    async def resolve(host, port):
        assert_external_io_allowed()
        calls.append((host, port))
        return list(addresses)

    monkeypatch.setattr(outbound, "OutboundPolicy", lambda rules: OutboundPolicy(rules, resolve))
    return addresses, calls


async def test_public_connection_authorizes_exact_scope_host_and_port(resolution):
    _, calls = resolution
    scope = Scope(channel_id="channel-one", environment="test")
    target = await outbound.validate_connection_target(scope, "https://new.example:8443/v1", [])
    assert (target.hostname, target.port, target.addresses) == (
        "new.example",
        8443,
        ("93.184.216.34",),
    )
    assert calls == [("new.example", 8443)]


async def test_private_destination_needs_connection_network_and_rechecks_dns(resolution):
    addresses, calls = resolution
    addresses[:] = ["10.20.0.5"]
    scope = Scope(channel_id="channel-one", environment="test")
    with pytest.raises(ServiceError, match="出站目的地"):
        await outbound.validate_connection_target(scope, "https://models.example", [])
    target = await outbound.validate_connection_target(
        scope, "https://models.example", ["10.20.0.0/16"]
    )
    assert target.addresses == ("10.20.0.5",)
    # 同一域名下一次解析改变或混入未授权地址，不能沿用上次授权结果。
    addresses.append("10.21.0.5")
    with pytest.raises(ServiceError):
        await outbound.validate_connection_target(scope, "https://models.example", ["10.20.0.0/16"])
    assert len(calls) == 3


async def test_proxy_fake_dns_networks_can_be_explicitly_configured(resolution):
    addresses, _ = resolution
    addresses[:] = ["198.18.0.36", "2001:2::59"]
    target = await outbound.validate_connection_target(
        Scope(channel_id="channel-one", environment="dev"),
        "https://api.deepseek.com",
        ["198.18.0.36/32", "2001:2::59/128"],
    )
    assert target.addresses == tuple(addresses)


@pytest.mark.parametrize(
    "network",
    [
        "127.0.0.1/32",
        "169.254.169.254/32",
        "100.100.100.200/32",
        "0.0.0.0/0",
        "::/0",
        "::1/128",
        "fe80::/10",
        "::ffff:127.0.0.1/128",
        "224.0.0.0/4",
        "invalid",
        "10.20.0.1/16",
    ],
)
async def test_reserved_or_invalid_networks_rejected_before_dns(resolution, network):
    _, calls = resolution
    with pytest.raises(ServiceError) as error:
        await outbound.validate_connection_target(
            Scope(channel_id="channel-one", environment="test"),
            "https://models.example",
            [network],
        )
    assert error.value.code == "MODEL_NETWORK_INVALID"
    assert not calls


@pytest.mark.parametrize(
    "endpoint",
    [
        "http://models.example",
        "https://user:pass@models.example",
        "https://models.example/../admin",
    ],
)
async def test_connection_url_cannot_bypass_https_or_path_checks(resolution, endpoint):
    _, calls = resolution
    with pytest.raises(ServiceError):
        await outbound.validate_connection_target(
            Scope(channel_id="channel-one", environment="test"), endpoint, []
        )
    assert not calls
