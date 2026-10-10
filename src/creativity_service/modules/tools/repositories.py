"""工具仓储与调用证据的短事务持久化。"""

from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncEngine

from creativity_service.core.context import AuthContext
from creativity_service.core.contracts import Attempt, ToolResult
from creativity_service.core.database import Repository, transaction
from creativity_service.core.database.soft_delete import active_rows
from creativity_service.core.database.tables import metadata as core_metadata
from creativity_service.core.deletion import ContentRef, DeletionGuard, content_key
from creativity_service.core.locking import record_key
from creativity_service.core.primitives import ServiceError, digest, utcnow
from creativity_service.integrations.tools import AdapterResult
from creativity_service.modules.tools.schemas import ToolExecution
from creativity_service.modules.tools.tables import metadata
from creativity_service.modules.tools.validation import artifact_references


class ToolRepository:
    def __init__(self, engine: AsyncEngine) -> None:
        self.engine = engine

    async def check_sources(self, context: AuthContext, raw: AdapterResult) -> None:
        file_ids = artifact_references(raw.data) | set(raw.artifact_ids)
        scope = context.scope
        async with transaction(self.engine, scope, [content_key(scope)]) as uow:
            await DeletionGuard(scope).check(
                uow,
                [
                    *(ContentRef("artifact", item) for item in file_ids),
                    *(ContentRef("evidence", e.evidence_id) for e in raw.evidence_refs),
                ],
            )
            artifacts = await Repository(core_metadata.tables["artifacts"], scope).get_many(
                uow.connection, file_ids
            )
            for item in file_ids:
                row = artifacts.get(item)
                if row is None or row["state"] != "AVAILABLE" or row["expires_at"] <= utcnow():
                    raise ServiceError(
                        "TOOL_RESULT_INVALID", "文件引用不存在、已过期或不在当前授权范围", 502
                    )
            refs = await Repository(metadata.tables["evidence_refs"], scope).get_many(
                uow.connection, [e.evidence_id for e in raw.evidence_refs]
            )
            for evidence in raw.evidence_refs:
                row = refs.get(evidence.evidence_id)
                if (
                    row is None
                    or row["source_id"] != evidence.source_id
                    or row["source_version"] != evidence.source_version
                    or row["source_type"] != evidence.source_type
                    or row["location"] != evidence.location.model_dump(mode="json")
                    or row["observed_at"] != evidence.observed_at
                    or row["authorization_scope"].get("principal_id") != context.principal_id
                ):
                    raise ServiceError(
                        "TOOL_RESULT_INVALID", "证据引用未经当前身份授权或内容不一致", 502
                    )

    async def resolve(
        self, context: AuthContext, version_id: str
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        scope = context.scope
        async with transaction(self.engine, scope, [content_key(scope)]) as uow:
            await DeletionGuard(scope).check(uow, [ContentRef("version", version_id)])
            version = await Repository(core_metadata.tables["resource_versions"], scope).get(
                uow.connection, version_id
            )
            if version is None or version["resource_type"] != "tool":
                raise ServiceError("NOT_FOUND", "工具版本不存在", 404)
            tool = await Repository(metadata.tables["tools"], scope).get(
                uow.connection, version["resource_id"]
            )
            if tool is None:
                raise ServiceError("NOT_FOUND", "工具不存在", 404)
            await DeletionGuard(scope).check(uow, [ContentRef("tool", tool["id"])])
            return tool, version

    async def check_evidence(self, context: AuthContext, result: ToolResult) -> None:
        scope = context.scope
        async with transaction(self.engine, scope, [content_key(scope)]) as uow:
            await DeletionGuard(scope).check(
                uow,
                [
                    ContentRef("version", result.tool_version_id),
                    *(ContentRef("evidence", e.evidence_id) for e in result.evidence_refs),
                ],
            )
            refs = await Repository(metadata.tables["evidence_refs"], scope).get_many(
                uow.connection, [e.evidence_id for e in result.evidence_refs]
            )
            for evidence in result.evidence_refs:
                row = refs.get(evidence.evidence_id)
                if (
                    row is None
                    or row["authorization_scope"].get("principal_id") != context.principal_id
                ):
                    raise ServiceError("TOOL_RESULT_INVALID", "缓存证据不存在或授权不符", 502)

    async def record(
        self,
        context: AuthContext,
        call: ToolExecution,
        call_id: str,
        tool_id: str,
        state: str,
        attempt: Attempt | None,
        result: ToolResult | None,
        error: dict[str, Any] | None,
        latency_ms: int | None,
        authorization_scope: dict[str, Any],
        *,
        source_run_id: str | None = None,
        resource_name: str | None = None,
    ) -> None:
        scope = context.scope
        refs = [ContentRef("run", call.run_id), ContentRef("version", call.tool_version_id)]
        edges = [(ref, ContentRef("tool_call", call_id)) for ref in refs]
        if source_run_id and source_run_id != call.run_id:
            # 缓存复用也复制了内容，来源运行必须进入持久化图，不能只依赖可选证据。
            refs.append(ContentRef("run", source_run_id))
            edges.append((ContentRef("run", source_run_id), ContentRef("tool_call", call_id)))
        if result:
            for file_id in artifact_references(result.data):
                edges.append((ContentRef("artifact", file_id), ContentRef("tool_call", call_id)))
            for evidence in result.evidence_refs:
                evidence_ref = ContentRef("evidence", evidence.evidence_id)
                call_ref = ContentRef("tool_call", call_id)
                # 缓存及既有证据是调用来源，不能倒置来源图污染原始证据的删除范围。
                edges.append(
                    (call_ref, evidence_ref)
                    if evidence.source_id == call_id and evidence.source_type == "tool_call"
                    else (evidence_ref, call_ref)
                )
        keys = [content_key(scope), record_key(scope.channel_id, "tool_calls", call_id)]
        keys += [
            record_key(
                scope.channel_id, "source_links", digest([call_id, a.resource_id, b.resource_id])
            )
            for a, b in edges
        ]
        keys += (
            [
                record_key(scope.channel_id, "evidence_refs", e.evidence_id)
                for e in result.evidence_refs
            ]
            if result
            else []
        )
        async with transaction(self.engine, scope, keys) as uow:
            await DeletionGuard(scope).check(uow, [*refs, ContentRef("tool_call", call_id)])
            repo = Repository(metadata.tables["tool_calls"], scope)
            values = dict(
                tool_id=tool_id,
                run_id=call.run_id,
                step_id=call.step_id,
                attempt_id=attempt.attempt_id if attempt else None,
                tool_version_id=call.tool_version_id,
                args_digest=digest(call.arguments),
                state=state,
                source_request_id=result.source_request_id
                if result
                else attempt.source_request_id
                if attempt
                else None,
                result_ref=None,
                latency_ms=latency_ms,
                error=error,
                # 原文不落库；保留字段结构供排障，摘要用于比对同参调用。
                redacted_arguments={name: "已脱敏" for name in call.arguments},
                result_summary=(
                    {
                        "type": type(result.data).__name__,
                        "truncated": result.truncated,
                        "has_more": result.has_more,
                        "observed_at": result.observed_at.isoformat(),
                        "source_version": result.source_version,
                        "data_digest": digest(result.data),
                    }
                    if result
                    else None
                ),
                evidence_ids=[e.evidence_id for e in result.evidence_refs] if result else [],
                attempt=attempt.model_dump(mode="json") if attempt else None,
            )
            previous = await repo.get(uow.connection, call_id)
            if previous:
                await repo.change(uow, call_id, previous["revision"], values)
            else:
                await repo.add(uow, call_id, values)
            links = Repository(core_metadata.tables["source_links"], scope)
            mapped = {
                digest([call_id, ref.resource_id, derived.resource_id]): (ref, derived)
                for ref, derived in edges
            }
            existing_links = await links.get_many(uow.connection, mapped)
            await DeletionGuard(scope).link_many(
                uow,
                [
                    (identifier, ref, derived, None)
                    for identifier, (ref, derived) in mapped.items()
                    if identifier not in existing_links
                ],
            )
            if result:
                repo = Repository(metadata.tables["evidence_refs"], scope)
                existing_evidence = await repo.get_many(
                    uow.connection, [e.evidence_id for e in result.evidence_refs]
                )
                await repo.add_many(
                    uow,
                    {
                        evidence.evidence_id: dict(
                            source_type=evidence.source_type,
                            source_id=evidence.source_id,
                            source_version=evidence.source_version,
                            observed_at=evidence.observed_at,
                            location=evidence.location.model_dump(mode="json"),
                            title=evidence.title,
                            artifact_id=None,
                            authorization_scope=authorization_scope,
                        )
                        for evidence in result.evidence_refs
                        if evidence.evidence_id not in existing_evidence
                    },
                )
        if state not in {"DENIED"}:
            from creativity_service.modules.resources.usage import record_use

            await record_use(
                self.engine, context, call.run_id, "tool", tool_id, resource_name=resource_name
            )

    async def rows(
        self, context: AuthContext, table_name: str, **filters: Any
    ) -> list[dict[str, Any]]:
        table = metadata.tables[table_name]
        repo = Repository(table, context.scope)
        async with self.engine.connect() as connection:
            statement = active_rows(
                select(table).where(
                    repo.predicate(), *(table.c[k] == v for k, v in filters.items())
                )
            )
            return [
                dict(row)
                for row in (
                    await connection.execute(
                        statement.order_by(table.c.created_at.desc()).limit(500)
                    )
                ).mappings()
            ]
