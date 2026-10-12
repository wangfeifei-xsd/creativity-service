"""验证 Qdrant 协议失败、集合兼容性和主体边界。"""

from unittest.mock import AsyncMock

import httpx
import pytest
from pydantic import SecretStr

from creativity_service.core.context import Scope
from creativity_service.core.primitives import ServiceError
from creativity_service.modules.memory.vector_store import QdrantStore


@pytest.mark.parametrize(
    "status,body",
    [
        (401, {"status": {"error": "凭据无效"}}),
        (500, {"status": "error"}),
        (200, {"status": "error", "result": []}),
        (200, {"status": "ok"}),
        (200, []),
    ],
)
async def test_transport_errors_fail_closed_without_credentials(
    settings, monkeypatch, status, body
):
    store = QdrantStore(settings.model_copy(update={"qdrant_api_key": SecretStr("test-api-key")}))
    client = httpx.AsyncClient

    def respond(request):
        assert request.headers["api-key"] == "test-api-key"
        assert request.method == "GET" and request.url.path == "/collections"
        return httpx.Response(status, json=body)

    monkeypatch.setattr(
        httpx,
        "AsyncClient",
        lambda **kwargs: client(**kwargs, transport=httpx.MockTransport(respond)),
    )
    with pytest.raises(ServiceError, match="Qdrant") as error:
        await store.health()
    assert "test-api-key" not in str(error.value)


@pytest.mark.parametrize(
    "vectors",
    [
        {"size": 3, "distance": "Cosine"},
        {"size": 2, "distance": "Dot"},
        {"named": {"size": 2, "distance": "Cosine"}},
    ],
)
async def test_incompatible_existing_collection_is_not_overwritten(settings, vectors):
    store = QdrantStore(settings)
    store.request = AsyncMock(return_value={"config": {"params": {"vectors": vectors}}})
    with pytest.raises(ServiceError, match="不匹配"):
        await store.ensure_collection(2)
    assert store.request.await_count == 1


async def test_upsert_rejects_foreign_scope_before_network(settings):
    store = QdrantStore(settings)
    store.request = AsyncMock()
    scope = Scope(channel_id="a", environment="test", subject_type="user", subject_id="same")
    with pytest.raises(ValueError, match="受信范围"):
        await store.upsert(scope, 2, [{**scope.model_dump(), "channel_id": "b"}])
    store.request.assert_not_awaited()


@pytest.mark.parametrize("operation", ["upsert", "delete", "search"])
async def test_subjectless_scope_is_rejected_even_for_empty_requests(settings, operation):
    store = QdrantStore(settings)
    store.request = AsyncMock()
    scope = Scope(channel_id="a", environment="test")
    with pytest.raises(ValueError, match="完整渠道"):
        if operation == "search":
            await store.search(scope, "model", [1, 0], [], 1)
        else:
            await getattr(store, operation)(scope, 2, [])
    store.request.assert_not_awaited()


@pytest.mark.parametrize("status", ["acknowledged", "clock_rejected", None])
def test_unconfirmed_writes_cannot_be_marked_synced(status):
    with pytest.raises(ServiceError, match="尚未确认"):
        QdrantStore.require_completed({"status": status})
