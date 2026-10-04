"""运行只缓存引用，实际使用前复核偏好、策略、来源与记忆版本。"""

import logging
import re
from datetime import datetime
from typing import Any

from sqlalchemy import func, or_, select
from sqlalchemy.exc import SQLAlchemyError

from creativity_service.core.context import AuthContext
from creativity_service.core.contracts import ToolResult
from creativity_service.core.database import Repository, UnitOfWork, transaction
from creativity_service.core.deletion import ContentRef, DeletionGuard
from creativity_service.core.primitives import ServiceError, digest, new_id, utcnow
from creativity_service.core.security.keys import scoped_key
from creativity_service.modules.memory import repositories as repo
from creativity_service.modules.memory.queries import MemoryQueries
from creativity_service.modules.memory.reading import MemoryReadData
from creativity_service.modules.memory.schemas import (
    CandidateInput,
    LoadedMemory,
    MemoryCreate,
    MemoryLoad,
    MemoryPolicy,
    MemoryRef,
    MemorySelection,
    MemoryView,
    SourceInput,
)
from creativity_service.modules.memory.tables import metadata
from creativity_service.modules.memory.writes import MemoryWrites
from creativity_service.modules.runs.tables import metadata as runs
from creativity_service.modules.tools.tables import metadata as tools

logger = logging.getLogger(__name__)
WARNING = "长期记忆暂不可用，本次未使用记忆；必要事实请通过业务工具获取"


class MemoryRuntime(MemoryWrites, MemoryQueries):
    @staticmethod
    def intersect_policy(current: MemoryPolicy, frozen: MemoryPolicy | None) -> MemoryPolicy:
        """冻结上限与当前策略取交集，后续放宽策略不扩张已受理运行。"""
        if frozen is None:
            return current
        return current.model_copy(
            update={
                "read_enabled": current.read_enabled and frozen.read_enabled,
                "suggest_enabled": current.suggest_enabled and frozen.suggest_enabled,
                "write_mode": min(
                    (current.write_mode, frozen.write_mode),
                    key=["DISABLED", "CANDIDATE", "EXPLICIT"].index,
                ),
                "max_items": min(current.max_items, frozen.max_items),
                "allowed_types": [t for t in current.allowed_types if t in frozen.allowed_types],
                "retrieval_limit": min(current.retrieval_limit, frozen.retrieval_limit),
                "ttl_seconds": min(current.ttl_seconds, frozen.ttl_seconds),
                "failure_mode": "FAIL"
                if "FAIL" in {current.failure_mode, frozen.failure_mode}
                else "OMIT",
            }
        )

    @staticmethod
    def reference_cache_key(context: AuthContext, agent_id: str, keys: list[str]) -> str:
        return scoped_key(context.scope, "memory", [agent_id, *sorted(set(keys))])

    @staticmethod
    def validate_keys(keys: list[str]) -> None:
        if len(keys) > 1100 or any(
            not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}", key) for key in keys
        ):
            raise ServiceError("MEMORY_ATTRIBUTE_NOT_ALLOWED", "检索属性不属于长期记忆范围", 422)

    async def runtime_run(
        self, uow: UnitOfWork, context: AuthContext, run_id: str
    ) -> dict[str, Any]:
        self.require_subject(context)
        row = await Repository(runs.tables["runs"], context.scope).get(uow.connection, run_id)
        if row is None:
            raise ServiceError("NOT_FOUND", "当前主体没有此运行", 404)
        if row["state"] not in {"QUEUED", "RUNNING"} or row["deadline"] <= utcnow():
            raise ServiceError("MEMORY_RUN_INACTIVE", "运行已失效，不能读写记忆", 409)
        await DeletionGuard(context.scope).check(uow, [ContentRef("run", run_id)])
        return row

    async def write_candidate(
        self, context: AuthContext, run_id: str, body: CandidateInput
    ) -> MemoryView | None:
        if body.memory_type == "FACT":
            raise ServiceError("MEMORY_AUTHORITY_REQUIRED", "稳定事实须由受控工具结果端口保存", 422)
        await self.authorization.require(context, "memory:write", "scope")
        await self.authorization.require(context, "run:create", run_id)
        async with transaction(self.engine, context.scope, repo.keys(context.scope)) as uow:
            run = await self.runtime_run(uow, context, run_id)
            policy = await self.policy(uow, context, run["agent_id"])
            if not await self.automatic_allowed(uow, context, run, body.key):
                return None
            if (
                body.intent == "TASK_ONLY"
                or not policy.suggest_enabled
                or policy.write_mode == "DISABLED"
                or not (await self.preference(uow, context, [])).enabled
            ):
                return None
            state = await self.sources.resolve(uow, context, body.source)
            if state is None or (
                body.source.source_type == "message" and state.authority != "USER"
            ):
                raise ServiceError("MEMORY_SOURCE_INVALID", "记忆来源不存在或不是用户输入", 422)
            if not await self.automatic_allowed(uow, context, run, body.key, state.observed_at):
                return None
            source = {
                **body.source.model_dump(),
                "evidence_id": body.source.source_id
                if body.source.source_type == "evidence"
                else None,
            }
            created = await self.persist(
                uow,
                context,
                MemoryCreate.model_validate(body.model_dump(exclude={"source", "intent"})),
                policy,
                source,
                state,
                confirmed=body.intent == "EXPLICIT",
                reason="CREATED" if body.intent == "EXPLICIT" else "INFERRED",
            )
            return await self.view(uow, context, created, [])

    async def write_fact(
        self, context: AuthContext, run_id: str, key: str, result: ToolResult
    ) -> MemoryView | None:
        """值从已登记的工具结果字段提取，模型不能自行改值或伪造来源背书。"""
        await self.authorization.require(context, "memory:write", "scope")
        await self.authorization.require(context, "run:create", run_id)
        if result.scope != context.scope or result.truncated:
            raise ServiceError("MEMORY_SOURCE_INVALID", "事实结果范围不符或内容不完整", 422)
        async with transaction(self.engine, context.scope, repo.keys(context.scope)) as uow:
            run = await self.runtime_run(uow, context, run_id)
            policy = await self.policy(uow, context, run["agent_id"])
            if not await self.automatic_allowed(uow, context, run, key):
                return None
            if (
                not policy.suggest_enabled
                or policy.write_mode == "DISABLED"
                or not (await self.preference(uow, context, [])).enabled
            ):
                return None
            calls = await Repository(tools.tables["tool_calls"], context.scope).find(
                uow.connection,
                run_id=run_id,
                tool_version_id=result.tool_version_id,
                state="SUCCEEDED",
            )
            supporting = [
                call
                for call in calls
                if call["source_request_id"] == result.source_request_id
                and (call["result_summary"] or {}).get("data_digest") == digest(result.data)
            ]
            for evidence in result.evidence_refs:
                path = evidence.location.field_path
                if (
                    not path
                    or path[-1] != key
                    or not any(evidence.evidence_id in call["evidence_ids"] for call in supporting)
                ):
                    continue
                stored = await Repository(tools.tables["evidence_refs"], context.scope).get(
                    uow.connection, evidence.evidence_id
                )
                if (
                    stored is None
                    or stored["location"] != evidence.location.model_dump(mode="json")
                    or stored["source_version"] != evidence.source_version
                ):
                    continue
                state = await self.sources.resolve(
                    uow,
                    context,
                    SourceInput(
                        source_type="evidence",
                        source_id=evidence.evidence_id,
                        source_version=evidence.source_version,
                    ),
                )
                if state is None:
                    continue
                if not await self.automatic_allowed(uow, context, run, key, state.observed_at):
                    return None
                value: Any = result.data
                try:
                    for part in path:
                        value = value[part]
                except (KeyError, IndexError, TypeError) as exc:
                    raise ServiceError(
                        "MEMORY_SOURCE_INVALID", "工具结果缺少证据定位的事实", 422
                    ) from exc
                created = await self.persist(
                    uow,
                    context,
                    MemoryCreate(key=key, memory_type="FACT", value=value),
                    policy,
                    {
                        "source_type": "evidence",
                        "source_id": evidence.evidence_id,
                        "source_version": evidence.source_version,
                        "evidence_id": evidence.evidence_id,
                    },
                    state,
                    confirmed=True,
                    reason="CREATED",
                )
                return await self.view(uow, context, created, [])
            raise ServiceError(
                "MEMORY_AUTHORITY_REQUIRED", "未找到当前运行已核验的事实工具结果", 422
            )

    @staticmethod
    def may_degrade(exc: Exception, policy: MemoryPolicy | None) -> bool:
        if policy is None or policy.failure_mode != "OMIT":
            return False
        if isinstance(exc, ServiceError):
            return exc.status >= 500
        return isinstance(exc, (SQLAlchemyError, TimeoutError, OSError))

    async def record_selection(
        self,
        uow: UnitOfWork,
        context: AuthContext,
        run: dict[str, Any],
        retrieval_id: str,
        refs: list[MemoryRef],
        keys: list[str],
        current_keys: list[str],
        warnings: list[str],
    ) -> None:
        await repo.save(
            uow,
            "memory_retrievals",
            retrieval_id,
            {
                "run_id": run["id"],
                "agent_id": run["agent_id"],
                "memory_refs": [r.model_dump() for r in refs],
                "selection_reason": {
                    "preference_revision": (await self.preference(uow, context, [])).revision,
                    "keys": sorted(set(keys)),
                    "current_keys": sorted(set(current_keys)),
                    "priority": "当前任务优先",
                },
                "warnings": warnings,
            },
        )

    async def select(
        self,
        context: AuthContext,
        run_id: str,
        keys: list[str],
        current_keys: list[str],
        *,
        frozen_policy: MemoryPolicy | None = None,
        for_embedding: bool = False,
    ) -> MemorySelection:
        self.validate_keys(keys)
        self.validate_keys(current_keys)
        await self.authorization.require(context, "memory:read", "scope")
        await self.authorization.require(context, "run:create", run_id)
        policy = None
        retrieval_id = new_id("memory_retrieval")
        try:
            async with transaction(self.engine, context.scope, repo.keys(context.scope)) as uow:
                run = await self.runtime_run(uow, context, run_id)
                policy = self.intersect_policy(
                    await self.effective_policy(uow, context, run["agent_id"], frozen_policy),
                    frozen_policy,
                )
                refs: list[MemoryRef] = []
                if policy.read_enabled and (await self.preference(uow, context, [])).enabled:
                    table = metadata.tables["memories"]
                    statement = select(table).where(
                        Repository(table, context.scope).predicate(),
                        table.c.status == "ACTIVE",
                        table.c.key.not_in(current_keys),
                    )
                    if keys:
                        statement = statement.where(table.c.key.in_(keys))
                    # 主体容量上限为 1000，保留原检索的 1100 条保护边界并复用本批来源。
                    items = [
                        dict(row)
                        for row in (
                            await uow.connection.execute(
                                statement.order_by(
                                    (table.c.memory_type != "ARCHIVE").desc(),
                                    table.c.observed_at.desc(),
                                    table.c.id,
                                ).limit(1100)
                            )
                        ).mappings()
                    ]
                    if not keys:
                        keys = list(dict.fromkeys(r["key"] for r in items))
                    data = await MemoryReadData.load(
                        uow, context, [(context, r) for r in items], None
                    )
                    grouped: dict[str, list[dict[str, Any]]] = {}
                    for row in items:
                        grouped.setdefault(row["key"], []).append(row)
                    for key in dict.fromkeys(keys):
                        active = [
                            r
                            for row in grouped.get(key, [])
                            if self.usable(r := await self.refresh(uow, context, row, data), policy)
                        ]
                        if len(active) > 1:
                            raise ServiceError("STORAGE_INVARIANT_BROKEN", "属性有效值冲突", 503)
                        if active:
                            refs.append(
                                MemoryRef(
                                    memory_id=active[0]["id"],
                                    version_id=active[0]["current_version_id"],
                                )
                            )
                        if len(refs) >= (
                            policy.max_items if for_embedding else policy.retrieval_limit
                        ):
                            break
                await self.record_selection(
                    uow, context, run, retrieval_id, refs, keys, current_keys, []
                )
            return MemorySelection(retrieval_id=retrieval_id, refs=refs, warnings=[])
        except (ServiceError, SQLAlchemyError, TimeoutError, OSError) as exc:
            if not self.may_degrade(exc, policy):
                raise
            logger.warning(
                "记忆检索降级",
                extra={
                    "channel_id": context.scope.channel_id,
                    "run_id": run_id,
                    "warnings": [WARNING],
                },
            )
            try:
                async with transaction(self.engine, context.scope, repo.keys(context.scope)) as uow:
                    await self.record_selection(
                        uow, context, run, retrieval_id, [], keys, current_keys, [WARNING]
                    )
            except (SQLAlchemyError, TimeoutError, OSError):
                logger.warning(
                    "记忆降级记录等待运行日志留存",
                    extra={"channel_id": context.scope.channel_id, "run_id": run_id},
                )
            return MemorySelection(retrieval_id=retrieval_id, refs=[], warnings=[WARNING])

    @staticmethod
    def usable(row: dict[str, Any], policy: MemoryPolicy) -> bool:
        return bool(
            row["status"] == "ACTIVE"
            and row["value"] is not None
            and (row["memory_type"] == "ARCHIVE" or row["memory_type"] in policy.allowed_types)
            and row["expires_at"] > utcnow()
            and (utcnow() - row["observed_at"]).total_seconds() < policy.ttl_seconds
        )

    async def load(
        self,
        context: AuthContext,
        run_id: str,
        selection: MemorySelection,
        current_keys: list[str],
        required_fact_keys: list[str],
        *,
        frozen_policy: MemoryPolicy | None = None,
    ) -> MemoryLoad:
        self.validate_keys(current_keys)
        self.validate_keys(required_fact_keys)
        await self.authorization.require(context, "memory:read", "scope")
        await self.authorization.require(context, "run:create", run_id)
        policy = None
        try:
            async with transaction(self.engine, context.scope, repo.keys(context.scope)) as uow:
                run = await self.runtime_run(uow, context, run_id)
                policy = self.intersect_policy(
                    await self.effective_policy(uow, context, run["agent_id"], frozen_policy),
                    frozen_policy,
                )
                stored = await repo.one(
                    uow.connection, "memory_retrievals", context.scope, id=selection.retrieval_id
                )
                if stored is None and not selection.refs and selection.warnings == [WARNING]:
                    return MemoryLoad(
                        items=[], warnings=[WARNING], required_tool_keys=required_fact_keys
                    )
                if (
                    not stored
                    or stored["run_id"] != run_id
                    or stored["memory_refs"] != [r.model_dump() for r in selection.refs]
                ):
                    raise ServiceError("MEMORY_SELECTION_INVALID", "记忆引用与当前运行不符", 409)
                result: list[LoadedMemory] = []
                preferences = await self.preference(uow, context, [])
                if (
                    policy.read_enabled
                    and preferences.enabled
                    and preferences.revision == stored["selection_reason"]["preference_revision"]
                ):
                    for ref in selection.refs[: policy.retrieval_limit]:
                        row = await repo.one(
                            uow.connection, "memories", context.scope, id=ref.memory_id
                        )
                        if row is None:
                            continue
                        row = await self.refresh(uow, context, row)
                        if (
                            not self.usable(row, policy)
                            or row["current_version_id"] != ref.version_id
                            or row["key"] in {*current_keys, *required_fact_keys}
                        ):
                            continue
                        result.append(
                            LoadedMemory(
                                memory_id=row["id"],
                                version_id=ref.version_id,
                                key=row["key"],
                                display_name=row["display_name"],
                                memory_type=row["memory_type"],
                                value=row["value"],
                                sources=await self.source_views(uow, context, row),
                            )
                        )
                        await repo.save(
                            uow, "memories", row["id"], {"usage_count": row["usage_count"] + 1}
                        )
                await repo.save(
                    uow,
                    "memory_retrievals",
                    stored["id"],
                    {
                        "selection_reason": {
                            **stored["selection_reason"],
                            "used_refs": [
                                {"memory_id": r.memory_id, "version_id": r.version_id}
                                for r in result
                            ],
                            "required_tool_keys": required_fact_keys,
                        }
                    },
                )
                return MemoryLoad(
                    items=result, warnings=stored["warnings"], required_tool_keys=required_fact_keys
                )
        except (ServiceError, SQLAlchemyError, TimeoutError, OSError) as exc:
            if not self.may_degrade(exc, policy):
                raise
            logger.warning(
                "记忆加载降级",
                extra={
                    "channel_id": context.scope.channel_id,
                    "run_id": run_id,
                    "warnings": [WARNING],
                },
            )
            return MemoryLoad(items=[], warnings=[WARNING], required_tool_keys=required_fact_keys)

    @staticmethod
    async def automatic_allowed(
        uow: UnitOfWork,
        context: AuthContext,
        run: dict[str, Any],
        key: str,
        observed_at: datetime | None = None,
    ) -> bool:
        return (await MemoryRuntime.automatic_allowed_many(uow, context, run, key, [observed_at]))[
            0
        ]

    @staticmethod
    async def automatic_allowed_many(
        uow: UnitOfWork,
        context: AuthContext,
        run: dict[str, Any],
        key: str,
        observed_at: list[datetime | None],
    ) -> list[bool]:
        """批量复核重新开启与遗忘水位，仅查询最新屏障，不展开历史删除任务。"""
        preference = await repo.one(uow.connection, "memory_preferences", context.scope)
        jobs, memories = metadata.tables["memory_deletion_jobs"], metadata.tables["memories"]
        matching_key = (
            select(memories.c.id)
            .where(
                Repository(memories, context.scope).predicate(),
                memories.c.key == key,
                jobs.c.memory_ids.op("?")(memories.c.id),
            )
            .exists()
        )
        cutoff = await uow.connection.scalar(
            select(func.max(jobs.c.created_at)).where(
                Repository(jobs, context.scope).predicate(),
                or_(jobs.c.kind == "CLEAR", matching_key),
            )
        )
        if preference:
            if not preference["enabled"]:
                return [False] * len(observed_at)
            cutoff = max(cutoff, preference["updated_at"]) if cutoff else preference["updated_at"]
        return [
            cutoff is None or cutoff < min(run["created_at"], value or run["created_at"])
            for value in observed_at
        ]
