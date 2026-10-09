"""模型连接不限制 IP 类型，检查与生成共用同一个连接客户端。"""

import httpx
import pytest

from creativity_service.core.database import assert_external_io_allowed
from creativity_service.core.primitives import ServiceError
from creativity_service.integrations.models.connection import ModelConnectionClient
from creativity_service.modules.models.policy import validate_endpoint
from tests.models.test_protocols import Credentials, context, fixture_config


@pytest.mark.parametrize(
    "addresses",
    [
        ["93.184.216.34"],
        ["10.20.0.5"],
        ["198.18.1.151", "2001:2::59"],
        ["127.0.0.1", "::1"],
        ["169.254.169.254"],
    ],
)
async def test_connection_accepts_resolved_addresses_without_network_configuration(addresses):
    config = fixture_config()
    calls, boundaries, requests = [], [], []

    async def resolve(host, port):
        assert_external_io_allowed()
        calls.append((host, port))
        return addresses

    async def handler(request):
        requests.append(request)
        return httpx.Response(200, json={"data": []})

    client = ModelConnectionClient(Credentials(), resolve, lambda: httpx.MockTransport(handler))

    async def before_send():
        boundaries.append(True)

    async def invoke(secret, http):
        assert http._transport.target.addresses == tuple(addresses)
        await http.get(config.endpoint + "/models")

    await client.call(context(config), config, invoke, before_send)
    assert calls == [("models.example", 443)]
    assert boundaries == [True] and len(requests) == 1


async def test_connection_resolves_each_call_and_requires_a_dns_result():
    config = fixture_config()
    addresses = ["198.18.1.151"]
    seen = []

    async def resolve(host, port):
        return list(addresses)

    async def before_send():
        pass

    async def invoke(secret, http):
        seen.append(http._transport.target.addresses)

    client = ModelConnectionClient(Credentials(), resolve)
    await client.call(context(config), config, invoke, before_send)
    addresses[:] = ["10.20.0.5"]
    await client.call(context(config), config, invoke, before_send)
    addresses.clear()
    with pytest.raises(ServiceError, match="解析失败"):
        await client.call(context(config), config, invoke, before_send)
    assert seen == [("198.18.1.151",), ("10.20.0.5",)]


@pytest.mark.parametrize(
    "endpoint",
    [
        "http://models.example",
        "https://user:pass@models.example",
        "https://models.example:invalid",
        "https://models.example:65536",
        "https://models.example:0",
        "https://models.example/../admin",
        "https://models.example/%2e%2e/admin",
        "https://models.example\\admin",
        "https://models.example/\nadmin",
        "https://models.example?key=secret",
        "https://models.example#fragment",
        "https://models.example/chat/completions",
    ],
)
def test_invalid_connection_settings_are_rejected_without_dns(endpoint):
    with pytest.raises(ServiceError) as error:
        validate_endpoint("chat_completions", endpoint)
    assert error.value.code == "MODEL_ENDPOINT_INVALID"
