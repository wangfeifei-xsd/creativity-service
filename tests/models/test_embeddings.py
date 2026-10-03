"""向量适配器仍验证预算、当前授权和实际计量，并拒绝不完整或无效向量。"""

import json

import httpx
import pytest

from creativity_service.core.primitives import ServiceError
from creativity_service.integrations.models.contracts import ModelRequest
from creativity_service.modules.memory.semantic import validate_embeddings
from tests.models.test_protocols import (
    adapter,
    context,
    fixture_attempt,
    fixture_config,
    fixture_reservation,
)


async def test_embedding_protocol_request_and_usage():
    config = fixture_config()
    requests = []

    def handle(request):
        requests.append(request)
        assert request.url.path == "/v1/embeddings"
        body = json.loads(request.content)
        assert body["input"] == ["用户偏好", "当前问题"]
        assert "max_tokens" not in body
        return httpx.Response(
            200,
            json={
                "object": "list",
                "model": "fixture-model",
                "data": [
                    {"object": "embedding", "index": 1, "embedding": [0.0, 1.0]},
                    {"object": "embedding", "index": 0, "embedding": [1.0, 0.0]},
                ],
                "usage": {"prompt_tokens": 6, "total_tokens": 6},
            },
            headers={"x-request-id": "embedded-one"},
        )

    events = [
        event
        async for event in adapter(handle).events(
            context(config),
            config,
            ModelRequest(
                operation="embedding",
                messages=[{"role": "user", "content": value} for value in ["用户偏好", "当前问题"]],
            ),
            fixture_attempt(config),
            fixture_reservation(config),
        )
    ]
    assert len(requests) == 1 and events[-1].kind == "completed"
    assert next(event for event in events if event.kind == "structured").structured[
        "embeddings"
    ] == [[1, 0], [0, 1]]
    usage = next(event.usage for event in events if event.kind == "usage")
    assert usage.status == "REPORTED" and usage.normalized_tokens["input"] == 6
    assert usage.normalized_tokens["output"] == 0 and usage.source_request_id == "embedded-one"


@pytest.mark.parametrize(
    "vectors", [[], [[0, 0]], [[float("nan"), 1]], [[True, 1]], [[1] * 4097], [[1], [1, 0]]]
)
def test_embedding_invalid_data(vectors):
    with pytest.raises(ServiceError):
        validate_embeddings(vectors, 1 if len(vectors) != 2 else 2)
