"""向量请求在事务外执行；写回和 pgvector 距离排序均限定已复核的完整主体范围。"""

from __future__ import annotations

import json
import math
from typing import TYPE_CHECKING, Any, cast

from sqlalchemy import text

from creativity_service.core.context import AuthContext
from creativity_service.core.database import Repository, transaction
from creativity_service.core.deletion import ContentRef, DeletionGuard
from creativity_service.core.locking import record_key
from creativity_service.core.primitives import ServiceError, digest
from creativity_service.integrations.models.contracts import ModelRequest
from creativity_service.modules.memory import repositories as repo
from creativity_service.modules.memory.embedding_tables import metadata
from creativity_service.modules.memory.reading import MemoryReadData
from creativity_service.modules.memory.schemas import MemoryPolicy, MemoryRef, MemorySelection
from creativity_service.modules.memory.tables import metadata as memories
from creativity_service.modules.models.schemas import FrozenModel

if TYPE_CHECKING:
    from creativity_service.modules.agents.schemas import FrozenExecutionSpec
    from creativity_service.modules.memory.services import MemoryService
    from creativity_service.modules.runs.schemas import Lease
    from creativity_service.modules.runtime.model import ModelRunner


def validate_embeddings(value: Any, count: int) -> list[list[float]]:
    if not isinstance(value, list) or len(value) != count:
        raise ServiceError("MODEL_OUTPUT_INVALID", "向量数量与输入不符", 502)
    size: int | None = None
    for vector in value:
        if not isinstance(vector, list) or not 1 <= len(vector) <= 4096:
            raise ServiceError("MODEL_OUTPUT_INVALID", "向量维度无效", 502)
        if any(
            type(v) not in {int, float} or not math.isfinite(v) or abs(v) > 1e10 for v in vector
        ):
            raise ServiceError("MODEL_OUTPUT_INVALID", "向量包含无效数值", 502)
        if not any(v != 0 for v in vector) or size is not None and size != len(vector):
            raise ServiceError("MODEL_OUTPUT_INVALID", "向量维度不一致或为零向量", 502)
        size = len(vector)
    return [[float(v) for v in vector] for vector in value]


async def semantic_selection(
    memory: MemoryService,
    runner: ModelRunner,
    context: AuthContext,
    lease: Lease,
    spec: FrozenExecutionSpec,
    node: str,
    query: str,
    current_keys: list[str],
    policy: MemoryPolicy,
) -> MemorySelection:
    selection = await memory.select(
        context,
        lease.run_id,
        [],
        current_keys,
        frozen_policy=policy,
        for_embedding=True,
    )
    if not selection.refs:
        return selection
    route = next(
        v for v in spec.versions if v.version_id == spec.definition.bindings.embedding_route_version
    )
    model = FrozenModel.model_validate(cast(list[dict[str, Any]], route.content["models"])[0])
    vectors = Repository(metadata.tables["memory_embeddings"], context.scope)
    memory_repo = Repository(memories.tables["memories"], context.scope)
    records: dict[str, dict[str, Any]] = {}
    missing: list[tuple[str, str]] = []
    async with transaction(memory.engine, context.scope, repo.keys(context.scope)) as uow:
        await memory.runtime_run(uow, context, lease.run_id)
        loaded = await memory_repo.get_many(uow.connection, [r.memory_id for r in selection.refs])
        data = await MemoryReadData.load(
            uow, context, [(context, r) for r in loaded.values()], None
        )
        for ref in selection.refs:
            row = loaded.get(ref.memory_id)
            if row is None:
                continue
            row = await memory.refresh(uow, context, row, data)
            if row["current_version_id"] != ref.version_id or not memory.usable(row, policy):
                continue
            identifier = digest(
                [
                    context.scope.model_dump(),
                    ref.memory_id,
                    ref.version_id,
                    model.model_version_id,
                    route.content_digest,
                ]
            )
            records[identifier] = row
        cached = await vectors.get_many(uow.connection, records)
        missing = [
            (
                identifier,
                json.dumps({"属性": row["display_name"], "值": row["value"]}, ensure_ascii=False),
            )
            for identifier, row in records.items()
            if identifier not in cached
        ]
    request = ModelRequest(
        operation="embedding",
        messages=[
            {"role": "user", "content": value}
            for value in [query, *[value for _, value in missing]]
        ],
    )
    output = await runner.invoke(
        context, lease, spec, f"{node}.memory", request, [model], [model.model_id]
    )
    if output.get("request_digest") != digest(request.model_dump(mode="json")):
        raise ServiceError("MEMORY_EMBEDDING_STALE", "语义检索来源已经变化，请重新运行", 503)
    generated = validate_embeddings(
        (output.get("structured") or {}).get("embeddings"), len(missing) + 1
    )
    keys = repo.keys(context.scope)
    for identifier, _ in missing:
        keys.extend(
            [
                record_key(context.scope.channel_id, "memory_embeddings", identifier),
                record_key(
                    context.scope.channel_id, "source_links", digest([identifier, "source"])
                ),
            ]
        )
    async with transaction(memory.engine, context.scope, keys) as uow:
        run = await memory.runtime_run(uow, context, lease.run_id)
        effective = memory.intersect_policy(
            await memory.effective_policy(uow, context, run["agent_id"], policy), policy
        )
        stored = await repo.required(
            uow.connection, "memory_retrievals", context.scope, id=selection.retrieval_id
        )
        preferences = await memory.preference(uow, context, [])
        valid: dict[str, dict[str, Any]] = {}
        if (
            effective.read_enabled
            and preferences.enabled
            and preferences.revision == stored["selection_reason"]["preference_revision"]
        ):
            loaded = await memory_repo.get_many(uow.connection, [r["id"] for r in records.values()])
            data = await MemoryReadData.load(
                uow, context, [(context, r) for r in loaded.values()], None
            )
            cached = await vectors.get_many(uow.connection, records)
            for identifier, old in records.items():
                row = loaded.get(old["id"])
                if row is None:
                    continue
                row = await memory.refresh(uow, context, row, data)
                if row["current_version_id"] == old["current_version_id"] and memory.usable(
                    row, effective
                ):
                    vector = cached.get(identifier)
                    if vector and vector["dimensions"] != len(generated[0]):
                        raise ServiceError(
                            "MEMORY_EMBEDDING_DIMENSION_MISMATCH",
                            "向量维度与当前模型版本的缓存不一致，请核查模型配置",
                            502,
                        )
                    valid[identifier] = row
        additions = {}
        links = []
        for (identifier, _), embedding in zip(missing, generated[1:], strict=True):
            if identifier not in valid or identifier in cached:
                continue
            row = valid[identifier]
            additions[identifier] = {
                "memory_id": row["id"],
                "memory_version_id": row["current_version_id"],
                "model_version_id": model.model_version_id,
                "run_id": lease.run_id,
                "dimensions": len(embedding),
                "embedding": embedding,
            }
            links.append(
                (
                    digest([identifier, "source"]),
                    ContentRef("memory", row["id"]),
                    ContentRef("memory_embedding", identifier),
                    row["current_version_id"],
                )
            )
        await vectors.add_many(uow, additions)
        await DeletionGuard(context.scope).link_many(uow, links)
        selected: list[MemoryRef] = []
        if valid:
            extension_schema = await uow.connection.scalar(
                text(
                    "SELECT n.nspname FROM pg_extension e JOIN pg_namespace n "
                    "ON n.oid=e.extnamespace WHERE e.extname='vector'"
                )
            )
            if not extension_schema:
                raise ServiceError("VECTOR_UNAVAILABLE", "数据库未安装向量检索扩展", 503)
            namespace = uow.connection.dialect.identifier_preparer.quote(extension_schema)
            # 物化边界先筛选完整范围和仍有效的版本，再计算距离，禁止全库召回后过滤。
            found = await uow.connection.execute(
                text(f"""
                WITH scoped AS MATERIALIZED (
                    SELECT id, memory_id, memory_version_id, embedding
                    FROM memory_embeddings
                    WHERE channel_id=:channel_id AND environment=:environment
                    AND subject_type IS NOT DISTINCT FROM :subject_type
                    AND subject_id IS NOT DISTINCT FROM :subject_id
                    AND model_version_id=:model_version AND dimensions=:dimensions
                    AND id = ANY(:identifiers)
                )
                SELECT memory_id, memory_version_id FROM scoped
                ORDER BY CAST(embedding::text AS {namespace}.vector)
                    OPERATOR({namespace}.<=>) CAST(:query AS {namespace}.vector), id
                LIMIT :limit
            """),
                {
                    **context.scope.model_dump(),
                    "model_version": model.model_version_id,
                    "dimensions": len(generated[0]),
                    "identifiers": list(valid),
                    "query": json.dumps(generated[0]),
                    "limit": effective.retrieval_limit,
                },
            )
            selected = [
                MemoryRef(memory_id=row.memory_id, version_id=row.memory_version_id)
                for row in found
            ]
        await repo.save(
            uow,
            "memory_retrievals",
            selection.retrieval_id,
            {
                "memory_refs": [ref.model_dump() for ref in selected],
                "selection_reason": {
                    **stored["selection_reason"],
                    "method": "semantic",
                    "model_version_id": model.model_version_id,
                },
            },
        )
        return selection.model_copy(update={"refs": selected})
