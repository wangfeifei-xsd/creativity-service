"""真实 Qdrant 距离、作用域隔离及迟到向量写回的删除屏障。"""

from types import SimpleNamespace

import pytest

from creativity_service.core.database import Repository, transaction
from creativity_service.core.deletion import ContentRef
from creativity_service.core.locking import record_key
from creativity_service.core.primitives import ServiceError, digest
from creativity_service.modules.memory.embedding_tables import metadata
from creativity_service.modules.memory.schemas import MemoryCreate, MemoryPolicy, MemoryUpdate
from creativity_service.modules.memory.semantic import semantic_selection
from tests.integration.memory.test_memory import budget
from tests.models.test_protocols import fixture_config

pytestmark = pytest.mark.integration


@pytest.fixture(autouse=True)
async def isolated_qdrant_collections(monkeypatch):
    from uuid import uuid4

    from creativity_service.modules.memory.vector_store import QdrantStore

    prefix = "test_memory_" + uuid4().hex
    monkeypatch.setenv("CREATIVITY_QDRANT_COLLECTION_PREFIX", prefix)
    store = QdrantStore()
    await store.health()
    try:
        yield
    finally:
        for collection in (await store.request("GET", "/collections"))["collections"]:
            name = collection["name"]
            if name.startswith(prefix + "_d"):
                await store.request("DELETE", f"/collections/{name}")


async def select_semantic(env, hook=None, model_version=None, dimensions=2):
    model = fixture_config().model_copy(update={"scope": env.context.scope})
    if model_version:
        model = model.model_copy(update={"model_version_id": model_version})
    spec = SimpleNamespace(
        versions=[
            SimpleNamespace(
                version_id="embedding-route",
                content={"models": [model.model_dump(mode="json")]},
                content_digest="route-configuration",
            )
        ],
        definition=SimpleNamespace(
            bindings=SimpleNamespace(embedding_route_version="embedding-route")
        ),
    )

    class Runner:
        async def invoke(self, context, lease, spec, node, request, models, order):
            if hook:
                await hook()
            return {
                "request_digest": digest(request.model_dump(mode="json")),
                "structured": {
                    "embeddings": [
                        ([1, 0] if "预算" in item["content"] else [0, 1]) + [0] * (dimensions - 2)
                        for item in request.messages
                    ]
                },
            }

    return await semantic_selection(
        env.memory,
        Runner(),
        env.context,
        SimpleNamespace(run_id=env.run_id),
        spec,
        "node",
        "预算",
        [],
        MemoryPolicy(retrieval_limit=1),
    )


async def test_semantic_sort_scope_and_forget(env):
    saved = await env.memory.create(env.context, MemoryCreate(key="usual_budget", value=budget()))
    await env.memory.create(env.context, MemoryCreate(key="play_style", value="休闲"))
    selected = await select_semantic(env)
    assert [item.memory_id for item in selected.refs] == [saved.memory_id]
    repository = Repository(metadata.tables["memory_embeddings"], env.context.scope)
    async with env.engine.connect() as connection:
        vectors = await repository.find(connection)
        vector = next(v for v in vectors if v["memory_id"] == saved.memory_id)
        assert len(vectors) == 2 and vector["memory_version_id"] == saved.version_id
        foreign = env.context.scope.model_copy(update={"channel_id": "other"})
        assert (
            await Repository(metadata.tables["memory_embeddings"], foreign).find(connection) == []
        )
    # 外部范围中的相似向量不能参与当前主体的召回和删除。
    for changed in (
        {"channel_id": "other"},
        {},
        {"subject_type": "member"},
        {"subject_id": "other"},
        {"environment": "prod"},
    ):
        scope = env.context.scope.model_copy(update=changed)
        identifier = digest([scope.model_dump(), vector["id"]])
        async with transaction(
            env.engine, scope, [record_key(scope.channel_id, "memory_embeddings", identifier)]
        ) as uow:
            await Repository(metadata.tables["memory_embeddings"], scope).add(
                uow,
                identifier,
                {
                    key: vector[key]
                    for key in (
                        "memory_id",
                        "memory_version_id",
                        "model_version_id",
                        "run_id",
                        "dimensions",
                        "embedding",
                    )
                }
                | {"memory_id": "foreign-memory"},
            )
        from creativity_service.modules.memory.vector_store import QdrantStore

        await QdrantStore().upsert(
            scope,
            vector["dimensions"],
            [{**vector, **scope.model_dump(), "id": identifier, "memory_id": "foreign-memory"}],
        )
    assert [r.memory_id for r in (await select_semantic(env)).refs] == [saved.memory_id]
    updated = await env.memory.update(
        env.context, saved.memory_id, MemoryUpdate(revision=saved.revision, value=budget(300))
    )
    assert (await select_semantic(env)).refs[0].version_id == updated.version_id
    assert (await select_semantic(env, model_version="new-embedding-version")).refs[
        0
    ].version_id == updated.version_id
    async with env.engine.connect() as connection:
        current = await repository.find(connection, memory_id=saved.memory_id)
        assert len(current) == 3
    await env.memory.forget(env.context, saved.memory_id)
    assert all(r.memory_id != saved.memory_id for r in (await select_semantic(env)).refs)
    from creativity_service.modules.data_lifecycle.handlers import ContentHandlers
    from tests.integration.conversations.conftest import Store

    cleanup = ContentHandlers(env.engine, Store(), env.runs)
    worker = env.context.model_copy(
        update={"principal_type": "worker", "principal_id": "data_lifecycle"}
    )
    for row in current:
        await cleanup.clean(worker, ContentRef("memory_embedding", row["id"]))
    async with env.engine.connect() as connection:
        assert await repository.find(connection, memory_id=saved.memory_id) == []


async def test_late_embedding_cannot_restore_deleted_memory(env):
    saved = await env.memory.create(env.context, MemoryCreate(key="usual_budget", value=budget()))

    async def forget():
        await env.memory.forget(env.context, saved.memory_id)

    try:
        result = await select_semantic(env, forget)
        assert result.refs == []
    except ServiceError as exc:
        assert exc.code in {"CONTENT_DELETED", "MEMORY_EMBEDDING_STALE"}
    async with env.engine.connect() as connection:
        assert (
            await Repository(metadata.tables["memory_embeddings"], env.context.scope).find(
                connection
            )
            == []
        )


async def test_embedding_dimension_drift_is_not_silent_empty_retrieval(env):
    saved = await env.memory.create(env.context, MemoryCreate(key="usual_budget", value=budget()))
    assert (await select_semantic(env)).refs[0].memory_id == saved.memory_id
    with pytest.raises(ServiceError) as failed:
        await select_semantic(env, dimensions=3)
    assert failed.value.code == "MEMORY_EMBEDDING_DIMENSION_MISMATCH"
    assert failed.value.status == 502
    # 新模型版本可建立独立维度空间，旧版本的有效缓存保持可用。
    assert (await select_semantic(env, model_version="new-dimensions", dimensions=3)).refs[
        0
    ].memory_id == saved.memory_id
    assert (await select_semantic(env)).refs[0].memory_id == saved.memory_id


async def test_vector_preparation_query_count_does_not_grow_per_memory(env):
    from creativity_service.modules.memory.schemas import MemoryAttribute, PolicyInput
    from tests.integration.agents.test_read_queries import statements

    current = await env.memory.get_policy(env.context)
    attributes = [
        MemoryAttribute(key=f"batch_{i}", label=f"偏好 {i}", value_schema={"type": "string"})
        for i in range(12)
    ]
    await env.memory.set_policy(
        env.context, PolicyInput(revision=current.revision, attributes=attributes)
    )
    await env.memory.create(env.context, MemoryCreate(key="batch_0", value="预算"))
    with statements(env.engine) as single:
        await select_semantic(env)
    for index in range(1, 12):
        await env.memory.create(env.context, MemoryCreate(key=f"batch_{index}", value="休闲"))
    with statements(env.engine) as multiple:
        await select_semantic(env)

    def reads(queries):
        return [q for q in queries if q.lstrip().startswith(("SELECT", "WITH"))]

    assert len(reads(multiple)) <= len(reads(single)) + 2
    assert sum("FROM memory_embeddings" in q for q in multiple) <= 4
    async with env.engine.connect() as connection:
        assert (
            len(
                await Repository(metadata.tables["memory_embeddings"], env.context.scope).find(
                    connection
                )
            )
            == 12
        )


async def test_forget_during_qdrant_search_is_checked_again(env, monkeypatch):
    from creativity_service.modules.memory.vector_store import QdrantStore

    saved = await env.memory.create(env.context, MemoryCreate(key="usual_budget", value=budget()))
    original = QdrantStore.search

    async def search(self, *args, **kwargs):
        found = await original(self, *args, **kwargs)
        await env.memory.forget(env.context, saved.memory_id)
        return found

    monkeypatch.setattr(QdrantStore, "search", search)
    assert (await select_semantic(env)).refs == []


async def test_durable_vector_retry_and_delete_overtakes_inflight_upsert(env):
    import asyncio

    from sqlalchemy import select, update

    from creativity_service.core.database import assert_external_io_allowed
    from creativity_service.core.deletion import content_key
    from creativity_service.core.primitives import utcnow
    from creativity_service.modules.memory.vector_store import QdrantStore
    from creativity_service.modules.memory.vector_sync import TASKS, VECTORS, VectorSync, enqueue

    saved = await env.memory.create(env.context, MemoryCreate(key="usual_budget", value=budget()))
    await select_semantic(env)
    scope = env.context.scope
    async with env.engine.connect() as connection:
        (vector,) = (
            (
                await connection.execute(
                    select(VECTORS).where(
                        VECTORS.c.channel_id == scope.channel_id,
                        VECTORS.c.memory_id == saved.memory_id,
                    )
                )
            )
            .mappings()
            .all()
        )
    identifier = vector["id"]

    async def pending():
        async with transaction(env.engine, scope, [content_key(scope)]) as uow:
            await uow.connection.execute(
                update(TASKS)
                .where(TASKS.c.channel_id == scope.channel_id, TASKS.c.id == identifier)
                .values(state="PENDING", next_attempt_at=utcnow())
            )

    class Unavailable(QdrantStore):
        async def upsert(self, *args):
            assert_external_io_allowed()
            raise OSError("模拟 Qdrant 不可达")

    await pending()
    with pytest.raises(ServiceError, match="后台将自动重试"):
        await VectorSync(env.engine, Unavailable()).sync(scope, [identifier])
    async with env.engine.connect() as connection:
        task = await Repository(TASKS, scope).get(connection, identifier)
        assert task["state"] == "PENDING" and task["attempts"] == 1
    await pending()
    started, release = asyncio.Event(), asyncio.Event()

    class Delayed(QdrantStore):
        async def upsert(self, *args):
            assert_external_io_allowed()
            started.set()
            await release.wait()
            await super().upsert(*args)

    worker = asyncio.create_task(VectorSync(env.engine, Delayed()).sync(scope, [identifier]))
    await asyncio.wait_for(started.wait(), 5)
    try:
        async with transaction(env.engine, scope, [content_key(scope)]) as uow:
            await enqueue(uow, [dict(vector)], delete=True)
        await VectorSync(env.engine).sync(scope, [identifier])
        with pytest.raises(ServiceError, match="仍在同步"):
            await VectorSync(env.engine).require_synced(scope, [identifier], "DELETE")
    finally:
        release.set()
        await worker
    await VectorSync(env.engine).sync(scope, [identifier])
    await VectorSync(env.engine).require_synced(scope, [identifier], "DELETE")
    assert (
        await QdrantStore().search(scope, vector["model_version_id"], [1, 0], [identifier], 1) == []
    )
    # 重建或迟到补写不能把已删除向量的墓碑改回 UPSERT。
    async with transaction(env.engine, scope, [content_key(scope)]) as uow:
        await enqueue(uow, [dict(vector)])
    await VectorSync(env.engine).require_synced(scope, [identifier], "DELETE")


async def test_qdrant_parallel_creation_same_id_scope_isolation_and_scoped_delete():
    import asyncio

    from creativity_service.core.context import Scope
    from creativity_service.modules.memory.vector_store import PAYLOAD_INDEXES, QdrantStore

    store = QdrantStore()
    own = Scope(
        channel_id="qdrant-test", environment="test", subject_type="user", subject_id="same"
    )
    scopes = [
        own,
        *(
            own.model_copy(update={key: value})
            for key, value in (
                ("channel_id", "other-channel"),
                ("environment", "prod"),
                ("subject_type", "device"),
                ("subject_id", "other-user"),
            )
        ),
    ]

    async def write(scope):
        await store.upsert(
            scope,
            2,
            [
                {
                    **scope.model_dump(),
                    "id": "same-id",
                    "memory_id": "memory",
                    "memory_version_id": "version",
                    "model_version_id": "model",
                    "embedding": [1, 0],
                }
            ],
        )

    # API 与 Worker 可以同时首次写入，相同业务标识也不能跨主体覆盖。
    await asyncio.gather(*(write(scope) for scope in scopes))
    info = await store.request("GET", f"/collections/{store.collection(2)}")
    assert info["config"]["params"]["vectors"] == {"size": 2, "distance": "Cosine"}
    assert set(PAYLOAD_INDEXES) <= set(info["payload_schema"])
    assert info["payload_schema"]["channel_id"]["params"]["is_tenant"] is True
    for scope in scopes:
        assert await store.search(scope, "model", [1, 0], ["same-id"], 10) == ["same-id"]
    assert await store.search(own, "old-model", [1, 0], ["same-id"], 10) == []
    assert await store.search(own, "model", [1, 0], ["other-id"], 10) == []
    await store.delete(own, 2, ["same-id"])
    await store.delete(own, 2, ["same-id"])
    assert await store.search(own, "model", [1, 0], ["same-id"], 10) == []
    for scope in scopes[1:]:
        assert await store.search(scope, "model", [1, 0], ["same-id"], 10) == ["same-id"]
    await store.delete(own, 3, ["same-id"])
