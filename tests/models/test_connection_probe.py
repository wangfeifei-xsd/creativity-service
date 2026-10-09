"""供应商连接检查的请求协议、失败文案及重定向保护。"""

import httpx
import pytest
from pydantic import SecretBytes

from creativity_service.core.primitives import ServiceError
from creativity_service.integrations.models.connection import ModelConnectionClient
from creativity_service.integrations.models.probe import probe_connection
from tests.models.test_protocols import Credentials, context, fixture_config, resolved


@pytest.mark.parametrize(
    "protocol,path",
    [
        ("chat_completions", "/v1/models"),
        ("anthropic_messages", "/v1/models"),
    ],
)
async def test_probe_uses_one_authenticated_catalog_request(protocol, path):
    config = fixture_config(protocol)
    seen = []

    async def handler(request):
        seen.append(request)
        assert request.method == "GET" and request.url.path == path
        if protocol == "chat_completions":
            assert request.headers["authorization"] == "Bearer fixture-secret"
        else:
            assert request.headers["x-api-key"] == "fixture-secret"
            assert request.headers["anthropic-version"] == "2023-06-01"
        return httpx.Response(200, json={"data": [{"id": "provider-model"}]})

    connection = ModelConnectionClient(
        Credentials(), resolved, lambda: httpx.MockTransport(handler)
    )

    async def before_send():
        pass

    async def probe(secret, client):
        await probe_connection(config, SecretBytes(b"fixture-secret"), client)

    await connection.call(context(config), config, probe, before_send)
    assert len(seen) == 1


@pytest.mark.parametrize(
    "status,body,code",
    [
        (401, {"error": "secret-provider-body"}, "MODEL_AUTH_FAILED"),
        (403, {}, "MODEL_AUTH_FAILED"),
        (429, {}, "MODEL_RATE_LIMITED"),
        (404, {}, "MODEL_PROBE_UNSUPPORTED"),
        (500, {}, "MODEL_PROVIDER_UNAVAILABLE"),
        (200, {"unexpected": "secret-provider-body"}, "MODEL_RESPONSE_INVALID"),
        (302, {}, "MODEL_REDIRECT_FORBIDDEN"),
    ],
)
async def test_probe_rejects_failure_without_echoing_provider_body(status, body, code):
    config = fixture_config()
    seen = []

    async def handler(request):
        seen.append(request)
        return httpx.Response(status, json=body, headers={"location": "https://untrusted.example"})

    connection = ModelConnectionClient(
        Credentials(), resolved, lambda: httpx.MockTransport(handler)
    )

    async def before_send():
        pass

    async def probe(secret, client):
        await probe_connection(config, secret, client)

    with pytest.raises(ServiceError) as error:
        await connection.call(context(config), config, probe, before_send)
    assert error.value.code == code
    assert "secret-provider-body" not in str(error.value)
    assert len(seen) == 1
