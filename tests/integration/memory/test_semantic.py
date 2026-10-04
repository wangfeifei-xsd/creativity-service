"""真实 pgvector 距离、作用域隔离及迟到向量写回的删除屏障。"""

from types import SimpleNamespace

import pytest
from sqlalchemy import text

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


async def select_semantic(env, hook=None, model_version=None, dimensions=2):
    model = fixture_config().model_copy(update={"scope": env.context.scope})
    if model_version:
        model = model.model_copy(update={"model_version_id": model_version})
    spec = SimpleNamespace(
        versions=[
            SimpleNamespace(
                version_id="embedding-route", content={"models": [model.model_dump(mode="json")]}
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
    async with env.engine.connect() as connection:
        installed = await connection.scalar(
            text("SELECT EXISTS (SELECT 1 FROM pg_extension WHERE extname='vector')")
        )
    if not installed:
        pytest.skip("向量集成验收需要安装 pgvector 的数据库")
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
        {"data_scope_id": "other"},
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
