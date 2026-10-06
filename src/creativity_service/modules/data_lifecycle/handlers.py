"""各类清理以持久化任务为权限边界，停用渠道或撤销操作人不撤销删除义务。"""

from typing import Any

from redis.asyncio import Redis
from sqlalchemy import delete, update
from sqlalchemy.ext.asyncio import AsyncEngine

from creativity_service.core.artifacts import ArtifactService, ObjectStore
from creativity_service.core.context import AuthContext, Scope
from creativity_service.core.database import Repository, UnitOfWork, transaction
from creativity_service.core.deletion import CleanupRegistry, ContentRef, DeletionGuard, content_key
from creativity_service.core.deletion.handlers import register_core_handlers
from creativity_service.core.primitives import ServiceError
from creativity_service.modules.data_lifecycle.graph import TARGETS
from creativity_service.modules.data_lifecycle.repository import put, rows
from creativity_service.modules.evaluations.independent import surviving_sources
from creativity_service.modules.memory.services import MemoryService
from creativity_service.modules.runs.schemas import TERMINAL
from creativity_service.modules.runs.services import RunService
from creativity_service.modules.usage.redaction import meter_only
from creativity_service.modules.usage.repositories import ledger_key
from creativity_service.storage import metadata


class CleanupAuthorization:
    async def require(self, context: AuthContext, action: str, resource_id: str) -> None:
        if (
            context.principal_type != "worker"
            or context.principal_id != "data_lifecycle"
            or action
            not in {
                "content:cleanup",
                "artifact:cleanup",
            }
        ):
            raise ServiceError("FORBIDDEN", "此身份仅供持久化清理任务使用", 403)


async def remove(uow: UnitOfWork, name: str, **filters: Any) -> None:
    if not isinstance(uow.scope, Scope):
        raise ValueError("清理需要业务范围")
    table = metadata.tables[name]
    await uow.connection.execute(
        delete(table).where(
            Repository(table, uow.scope).predicate(),
            *(table.c[k] == v for k, v in filters.items()),
        )
    )


class ContentHandlers:
    def __init__(
        self,
        engine: AsyncEngine,
        store: ObjectStore,
        runs: RunService,
        redis: Redis | None = None,
        prefix: str = "creativity",
    ) -> None:
        self.engine, self.store, self.runs, self.redis, self.prefix = (
            engine,
            store,
            runs,
            redis,
            prefix,
        )
        self.authorization = CleanupAuthorization()
        self.artifacts = ArtifactService(engine, store, self.authorization)
        self.memory = MemoryService(engine, self.authorization)
        self.registry = CleanupRegistry()
        register_core_handlers(self.registry, engine, self.artifacts, self.authorization)
        self.registry.register("memory", self.memory.clean)
        for kind in TARGETS.keys() - self.registry.handlers.keys():
            self.registry.register(kind, self.clean)

    async def invalidate_cache(self, channel_id: str) -> None:
        if self.redis is None:
            return
        # 内容缓存按渠道失效；仅保存引用的记忆缓存仍须经过当前有效性复核。
        async for key in self.redis.scan_iter(
            match=f"{self.prefix}:tools:{channel_id}:*", count=100
        ):
            await self.redis.delete(key)

    async def cancel(self, context: AuthContext, identifier: str) -> None:
        async with self.engine.connect() as connection:
            found = await rows(connection, context.scope.channel_id, "runs", id=identifier)
        if not found:
            return
        run = found[0]
        async with transaction(
            self.engine, context.scope, self.runs.keys(context, identifier, run["conversation_id"])
        ) as uow:
            run = await self.runs.locked_run(uow, identifier)
            if run["state"] not in TERMINAL:
                if run["state"] == "RUNNING":
                    await self.runs.transition(uow, run, "CANCEL_REQUESTED")
                await self.runs.transition(uow, run, "CANCELLED")
        await self.runs.after_commit(run)

    async def clean(self, context: AuthContext, ref: ContentRef) -> None:
        await self.authorization.require(context, "content:cleanup", ref.resource_id)
        if ref.resource_type == "run":
            await self.cancel(context, ref.resource_id)
        object_key = None
        async with transaction(
            self.engine,
            context.scope,
            [content_key(context.scope), ledger_key(context.scope.channel_id)],
        ) as uow:
            try:
                await DeletionGuard(context.scope).check(uow, [ref])
            except ServiceError as exc:
                if exc.code != "CONTENT_DELETED":
                    raise
            else:
                jobs = await rows(
                    uow.connection,
                    context.scope.channel_id,
                    "deletion_work_items",
                    target_type=ref.resource_type,
                    target_id=ref.resource_id,
                    state="RUNNING",
                )
                if (
                    ref.resource_type not in {"evaluation", "evaluation_dataset_version"}
                    or not jobs
                ):
                    raise ServiceError("DELETION_MARKER_REQUIRED", "清理前必须登记删除标记", 409)
            name = TARGETS[ref.resource_type]
            row = await Repository(metadata.tables[name], context.scope).get(
                uow.connection, ref.resource_id
            )
            if not row:
                return
            values: dict[str, Any] = {}
            if ref.resource_type == "conversation":
                for table in (
                    "messages",
                    "conversation_turns",
                    "conversation_summaries",
                    "context_snapshots",
                ):
                    await remove(uow, table, conversation_id=row["id"])
                values = {
                    "title": "已删除会话",
                    "subject_name": None,
                    "active_run_id": None,
                    "input_schema": {},
                    "status": "DELETING",
                }
            elif ref.resource_type in {
                "message",
                "summary",
                "context",
                "skill_file",
                "memory_embedding",
                "oauth_flow",
                "oauth_token",
                "credential",
                "provider_statement",
            }:
                await remove(uow, name, id=row["id"])
                return
            elif ref.resource_type in {
                "schedule",
                "batch",
                "batch_item",
                "webhook_endpoint",
                "webhook_delivery",
                "alert_rule",
            }:
                values = {"state": "DELETED"}
                for field, empty in (
                    ("spec", {}),
                    ("request", {}),
                    ("payload", {}),
                    ("pending_events", []),
                    ("identity", {}),
                    ("name", "已删除"),
                    ("url", "已删除"),
                    ("error", None),
                    ("last_error", None),
                ):
                    if field in row:
                        values[field] = empty
            elif ref.resource_type == "run":
                # 保留累计使用事实，随运行清理移除其中可识别的名称证据。
                uses = metadata.tables["resource_uses"]
                await uow.connection.execute(
                    update(uses)
                    .where(Repository(uses, context.scope).predicate(), uses.c.run_id == row["id"])
                    .values(resource_name="已清理资源", agent_name=None, caller_name=None)
                )
                for table in ("run_contents", "checkpoints", "run_events", "memory_retrievals"):
                    await remove(uow, table, run_id=row["id"])
                for attempt in await rows(
                    uow.connection, context.scope.channel_id, "attempts", run_id=row["id"]
                ):
                    await put(uow, "attempts", attempt["id"], {"error": None})
                for usage in await rows(
                    uow.connection, context.scope.channel_id, "usage_records", run_id=row["id"]
                ):
                    for event in await rows(
                        uow.connection,
                        context.scope.channel_id,
                        "usage_events",
                        attempt_id=usage["attempt_id"],
                    ):
                        await put(
                            uow,
                            "usage_events",
                            event["id"],
                            {
                                "raw_usage": meter_only(event["raw_usage"]),
                                "event_payload": {
                                    **event["event_payload"],
                                    "raw_usage": meter_only(event["raw_usage"]),
                                },
                            },
                        )
                for turn in await rows(
                    uow.connection, context.scope.channel_id, "conversation_turns", run_id=row["id"]
                ):
                    await put(
                        uow,
                        "conversation_turns",
                        turn["id"],
                        {
                            "input": {},
                            "confirmed_conditions": {},
                            "input_schema": {},
                            "output_schema": {},
                        },
                    )
                values = {
                    "result_ref": None,
                    "partial_output_ref": None,
                    "error": None,
                }
            elif ref.resource_type == "tool_call":
                values = {
                    "redacted_arguments": {},
                    "result_summary": None,
                    "error": None,
                    "attempt": None,
                    "evidence_ids": [],
                    "result_ref": None,
                }
            elif ref.resource_type == "evidence":
                values = {
                    "location": {},
                    "title": None,
                    "artifact_id": None,
                    "authorization_scope": {},
                }
            elif ref.resource_type == "usage_export":
                object_key = row["object_key"]
                values = {
                    "state": "DELETED",
                    "filters": {},
                    "metadata": {},
                    "scope_snapshot": {"scopes": []},
                    "error_message": None,
                }
            elif ref.resource_type in {"evaluation_case", "evaluation_fixture"}:
                values = {"payload": None, "invalidated": True}
                if ref.resource_type == "evaluation_case":
                    values.update(title="来源已删除的样本", case_key=row["id"])
                    direct = await Repository(
                        metadata.tables["deletion_markers"], context.scope
                    ).find(uow.connection, target_type="evaluation_case", target_id=row["id"])
                    links = await rows(
                        uow.connection,
                        context.scope.channel_id,
                        "source_links",
                        derived_type="evaluation_case",
                        derived_id=row["id"],
                    )
                    surviving = await surviving_sources(
                        uow, context.scope, row["payload"] or {}, links
                    )
                    if surviving and not direct:
                        allowed = {(s["resource_type"], s["resource_id"]) for s in surviving}
                        for link in links:
                            if (link["source_type"], link["source_id"]) not in allowed:
                                await remove(uow, "source_links", id=link["id"])
                        payload = {
                            **row["payload"],
                            "source_refs": surviving,
                            "human_label": None,
                            "assertions": [
                                {**a, "name": f"独立依据断言 {index + 1}"}
                                for index, a in enumerate(row["payload"]["assertions"])
                            ],
                            "labels": [],
                            "label_source": "独立有效运行",
                            "title": "已更新来源的样本",
                            "case_key": row["id"],
                        }
                        values.update(payload=payload, invalidated=False, title=payload["title"])
            elif ref.resource_type == "evaluation_result":
                values = {"judgment": None, "human_label": None, "state": "INVALID"}
            elif ref.resource_type == "evaluation":
                values = {"human_review": None, "name": "来源已删除的评测", "state": "CANCELLED"}
                for report in await rows(
                    uow.connection,
                    context.scope.channel_id,
                    "evaluation_reports",
                    evaluation_id=row["id"],
                ):
                    await put(
                        uow,
                        "evaluation_reports",
                        report["id"],
                        {"payload": {}, "reproducible": False},
                    )
            elif ref.resource_type == "evaluation_dataset_version":
                values = {"version_label": "来源已变更的版本"}
            elif ref.resource_type == "prompt_sample":
                values = {"title": "已删除样本", "input": {}, "expected_constraints": {}}
            elif ref.resource_type == "prompt_test":
                values = {"frozen_version": {}, "sample_snapshot": {}, "rendered_input": {}}
            elif ref.resource_type == "model_test":
                values = {"cases": [], "results": [], "execution": {}, "reason": None}
            elif ref.resource_type == "skill_test":
                values = {"context_snapshot": {}, "selected_files": [], "result": {}}
            elif ref.resource_type == "agent_candidate":
                values = {"spec": {}}
            else:
                raise ServiceError("CLEANUP_HANDLER_MISSING", "内容处理器未实现", 503)
            await put(uow, name, row["id"], values)
        if object_key:
            await self.store.delete(object_key)
