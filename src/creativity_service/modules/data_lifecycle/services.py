"""分批清理、租约重试和完成证明；队列只负责唤醒数据库中的删除任务。"""

from collections import Counter
from datetime import timedelta
from typing import Any

from sqlalchemy import insert, select
from sqlalchemy.ext.asyncio import AsyncEngine

from creativity_service.core.context import AuthContext, Authorization, Scope
from creativity_service.core.database import (
    Repository,
    UnitOfWork,
    scope_values,
    transaction,
    validate_row,
)
from creativity_service.core.deletion import ContentRef, DeletionService, content_key
from creativity_service.core.deletion.ledger import DeletionLedger, maintenance_mode
from creativity_service.core.primitives import ServiceError, digest, new_id, utcnow
from creativity_service.modules.data_lifecycle.graph import LABELS, TARGETS, affected, locate
from creativity_service.modules.data_lifecycle.handlers import ContentHandlers
from creativity_service.modules.data_lifecycle.repository import (
    put,
    related_rows,
    rows,
    scope_of,
    worker_context,
)
from creativity_service.modules.iam.reading import read_actions, read_policy, require_action
from creativity_service.storage import metadata


class DataLifecycleService:
    def __init__(
        self, engine: AsyncEngine, handlers: ContentHandlers, authorization: Authorization
    ) -> None:
        self.engine, self.handlers, self.authorization = engine, handlers, authorization

    async def target_context(self, context: AuthContext, kind: str, identifier: str) -> AuthContext:
        if kind not in TARGETS:
            raise ServiceError("VALIDATION_ERROR", "不支持此类内容删除", 422)
        async with self.engine.connect() as connection:
            found = await rows(connection, context.scope.channel_id, TARGETS[kind], id=identifier)
        if len(found) != 1:
            raise ServiceError("NOT_FOUND", "当前范围没有此内容", 404)
        own = scope_of(found[0])
        if own != context.scope:
            if (
                context.principal_type != "management"
                or context.scope.subject_id
                or own.environment != context.scope.environment
            ):
                raise ServiceError("NOT_FOUND", "当前范围没有此内容", 404)
            context = context.model_copy(update={"scope": own})
        return context

    async def preview(self, context: AuthContext, kind: str, identifier: str) -> dict[str, Any]:
        context = await self.target_context(context, kind, identifier)
        await self.authorization.require(context, "content:delete", "scope")
        async with transaction(self.engine, context.scope, [content_key(context.scope)]) as uow:
            await locate(uow, context.scope, kind, identifier)
            resources = await affected(uow, context.scope, kind, identifier)
        counts = Counter(r["resource_type"] for r in resources)
        return {
            "resources": [
                {"resource_type": k, "label": LABELS.get(k, "关联内容"), "count": v}
                for k, v in sorted(counts.items())
            ],
            "shared_memories": sum(
                r["mode"] == "UPDATE_SOURCES" and r["resource_type"] == "memory" for r in resources
            ),
            "explanation": "删除立即阻断访问与恢复；独立有效的记忆依据经复核后保留。",
        }

    async def request(self, context: AuthContext, kind: str, identifier: str) -> dict[str, Any]:
        context = await self.target_context(context, kind, identifier)
        await self.preview(context, kind, identifier)

        # 身份和目标已由服务端校验；公共登记使用相同内容图锁。
        class VerifiedDelete:
            async def require(self, value: AuthContext, action: str, resource_id: str) -> None:
                if value != context or action != "content:delete" or resource_id != identifier:
                    raise ServiceError("FORBIDDEN", "删除范围不符", 403)

        marker_id = await DeletionService(self.engine, VerifiedDelete()).mark(
            context, ContentRef(kind, identifier), "CONTENT_REQUESTED"
        )
        await self.prepare_channel(context.scope.channel_id)
        async with self.engine.connect() as connection:
            jobs = await rows(
                connection, context.scope.channel_id, "deletion_jobs", marker_id=marker_id
            )
        return await self.progress(context, jobs[0]["id"])

    async def import_ledger(self, channel_id: str, *, required: bool = False) -> dict[str, Any]:
        manifest = await DeletionLedger().operate(channel_id, required=required)
        table = metadata.tables["deletion_markers"]
        control = Scope(channel_id=channel_id, environment="dev")
        for start in range(0, len(manifest["entries"]), 200):
            entries = manifest["entries"][start : start + 200]
            async with transaction(self.engine, control, [content_key(control)]) as uow:
                existing = {
                    r["id"]: dict(r)
                    for r in (
                        await uow.connection.execute(
                            select(table).where(
                                table.c.channel_id == channel_id,
                                table.c.id.in_([entry["id"] for entry in entries]),
                            )
                        )
                    ).mappings()
                }
                additions = {}
                for entry in entries:
                    scope = Scope.model_validate(entry["scope"])
                    if scope.channel_id != channel_id:
                        raise ServiceError("DELETION_LEDGER_INVALID", "删除清单渠道不一致", 503)
                    old = existing.get(entry["id"])
                    if old is not None:
                        if scope_of(old) != scope:
                            raise ServiceError("SCOPE_MISMATCH", "清理记录归属不一致", 403)
                        continue
                    now = utcnow()
                    row = {
                        "is_deleted": False,
                        **scope_values(table, scope),
                        "id": entry["id"],
                        "created_at": now,
                        "updated_at": now,
                        "revision": 1,
                        **{
                            key: entry[key]
                            for key in ("target_type", "target_id", "reason_code", "requested_by")
                        },
                    }
                    validate_row(table, row)
                    additions[entry["id"]] = row
                if additions:
                    await uow.connection.execute(insert(table).values(list(additions.values())))
        return manifest

    async def prepare_channel(self, channel_id: str, limit: int = 100) -> None:
        await self.import_ledger(channel_id)
        async with self.engine.connect() as connection:
            markers = await rows(connection, channel_id, "deletion_markers")
            jobs = await rows(connection, channel_id, "deletion_jobs")
        prepared = {
            j["marker_id"]
            for j in jobs
            if j["affected_resources"] and any("scope" in r for r in j["affected_resources"])
        }
        count = 0
        for marker in markers:
            if marker["reason_code"] != "LIFECYCLE_DERIVED":
                await DeletionLedger().record(
                    scope_of(marker),
                    marker["target_type"],
                    marker["target_id"],
                    marker["reason_code"],
                    marker["requested_by"],
                )
            if marker["reason_code"] == "LIFECYCLE_DERIVED" or marker["id"] in prepared:
                continue
            await self.prepare(marker)
            count += 1
            if count >= limit:
                break

    async def prepare(self, marker: dict[str, Any]) -> None:
        scope = scope_of(marker)
        async with transaction(self.engine, scope, [content_key(scope)]) as uow:
            jobs = await rows(
                uow.connection, scope.channel_id, "deletion_jobs", marker_id=marker["id"]
            )
            job_id = jobs[0]["id"] if jobs else marker["id"]
            if jobs and any("scope" in r for r in jobs[0]["affected_resources"]):
                return
            resources = await affected(
                uow,
                scope,
                marker["target_type"],
                marker["target_id"],
                [(r["resource_type"], r["resource_id"]) for r in jobs[0]["affected_resources"]]
                if jobs
                else None,
            )
            # 在来源处理器移除边之前固化后代标记，旧快照不能因来源集合变小而复活。
            for ref in resources:
                own = Scope.model_validate(ref["scope"])
                inner = UnitOfWork(uow.connection, own, uow.keys)
                if ref["mode"] == "DELETE":
                    mid = digest([own.model_dump(), ref["resource_type"], ref["resource_id"]])
                    if not await Repository(
                        metadata.tables["deletion_markers"], own, include_deleted=True
                    ).get(uow.connection, mid):
                        await put(
                            inner,
                            "deletion_markers",
                            mid,
                            {
                                "target_type": ref["resource_type"],
                                "target_id": ref["resource_id"],
                                "reason_code": "LIFECYCLE_DERIVED",
                                "requested_by": marker["requested_by"],
                            },
                        )
                await self.add_item(inner, job_id, ref["resource_type"], ref["resource_id"])
            await self.add_item(uow, job_id, "cache", scope.channel_id)
            await put(
                uow,
                "deletion_jobs",
                job_id,
                {
                    "marker_id": marker["id"],
                    "conversation_id": marker["target_id"]
                    if marker["target_type"] == "conversation"
                    else None,
                    "scope_description": {
                        "resource_type": marker["target_type"],
                        "resource_id": marker["target_id"],
                    },
                    "affected_resources": [
                        *resources,
                        {
                            "resource_type": "cache",
                            "resource_id": scope.channel_id,
                            "scope": scope.model_dump(),
                            "mode": "DELETE",
                        },
                    ],
                    "state": "PENDING",
                    "completed_at": None,
                },
            )

    @staticmethod
    async def add_item(uow: UnitOfWork, job_id: str, kind: str, identifier: str) -> None:
        item_id = digest([job_id, kind, identifier])
        if not await rows(uow.connection, uow.scope.channel_id, "deletion_work_items", id=item_id):
            await put(
                uow,
                "deletion_work_items",
                item_id,
                {
                    "job_id": job_id,
                    "handler_key": kind,
                    "target_type": kind,
                    "target_id": identifier,
                    "state": "PENDING",
                    "attempts": 0,
                    "next_attempt_at": utcnow(),
                    "last_error": None,
                    "lease_token": None,
                    "lease_until": None,
                    "completed_at": None,
                },
            )

    async def execute_item(self, item: dict[str, Any]) -> None:
        scope = scope_of(item)
        token = new_id("cleanup")
        async with transaction(self.engine, scope, [content_key(scope)]) as uow:
            repo = Repository(metadata.tables["deletion_work_items"], scope, include_deleted=True)
            current = await repo.get(uow.connection, item["id"])
            if (
                not current
                or current["state"] == "COMPLETED"
                or current["next_attempt_at"] > utcnow()
                or (current["lease_until"] and current["lease_until"] > utcnow())
            ):
                return
            item = await put(
                uow,
                "deletion_work_items",
                item["id"],
                {
                    "state": "RUNNING",
                    "attempts": current["attempts"] + 1,
                    "lease_token": token,
                    "lease_until": utcnow() + timedelta(minutes=5),
                },
            )
        error = None
        try:
            if item["target_type"] == "cache":
                await self.handlers.invalidate_cache(scope.channel_id)
            else:
                await self.handlers.registry.clean(
                    worker_context(scope), ContentRef(item["target_type"], item["target_id"])
                )
        except Exception as exc:
            error = exc.code if isinstance(exc, ServiceError) else "CLEANUP_FAILED"
        async with transaction(self.engine, scope, [content_key(scope)]) as uow:
            current = await Repository(
                metadata.tables["deletion_work_items"], scope, include_deleted=True
            ).get(uow.connection, item["id"])
            if current and current["lease_token"] == token:
                await put(
                    uow,
                    "deletion_work_items",
                    item["id"],
                    {
                        "state": "FAILED" if error else "COMPLETED",
                        "last_error": error,
                        "lease_token": None,
                        "lease_until": None,
                        "completed_at": None if error else utcnow(),
                        "next_attempt_at": utcnow()
                        + timedelta(seconds=min(3600, 2 ** min(item["attempts"], 11)))
                        if error
                        else utcnow(),
                    },
                )

    async def finish(self, channel_id: str) -> None:
        async with self.engine.connect() as connection:
            jobs = await rows(connection, channel_id, "deletion_jobs")
        for job in jobs:
            scope = scope_of(job)
            async with transaction(self.engine, scope, [content_key(scope)]) as uow:
                items = await rows(
                    uow.connection, channel_id, "deletion_work_items", job_id=job["id"]
                )
                if not items or job["state"] == "COMPLETED":
                    continue
                complete = all(i["state"] == "COMPLETED" for i in items)
                state = (
                    "COMPLETED"
                    if complete
                    else "FAILED"
                    if any(i["state"] == "FAILED" for i in items)
                    else "PENDING"
                )
                await put(
                    uow,
                    "deletion_jobs",
                    job["id"],
                    {"state": state, "completed_at": utcnow() if complete else None},
                )
                if complete:
                    proof = {
                        "job_id": job["id"],
                        "marker_digest": digest(job["affected_resources"]),
                        "counts": dict(Counter(i["target_type"] for i in items)),
                        "completed_at": utcnow(),
                    }
                    await put(
                        uow,
                        "deletion_receipts",
                        job["id"],
                        {
                            **proof,
                            "proof_digest": digest(
                                {**proof, "completed_at": proof["completed_at"].isoformat()}
                            ),
                        },
                    )
                    if job["conversation_id"]:
                        await put(
                            uow,
                            "conversations",
                            job["conversation_id"],
                            {"is_deleted": True, "status": "DELETED"},
                        )
        await self.finish_memory_jobs(channel_id)

    async def finish_memory_jobs(self, channel_id: str) -> None:
        async with self.engine.connect() as connection:
            jobs = await rows(connection, channel_id, "memory_deletion_jobs")
        for job in jobs:
            if job["state"] == "COMPLETED":
                continue
            scope = scope_of(job)
            async with transaction(self.engine, scope, [content_key(scope)]) as uow:
                states = []
                for identifier in job["memory_ids"]:
                    marker_id = digest([scope.model_dump(), "memory", identifier])
                    found = await rows(
                        uow.connection, channel_id, "deletion_jobs", marker_id=marker_id
                    )
                    states.append(found[0]["state"] if found else "PENDING")
                done = all(state == "COMPLETED" for state in states)
                await put(
                    uow,
                    "memory_deletion_jobs",
                    job["id"],
                    {
                        "state": "COMPLETED"
                        if done
                        else "FAILED"
                        if "FAILED" in states
                        else "WAITING_PROPAGATION",
                        "completed_at": utcnow() if done else None,
                    },
                )

    async def sweep(self, channel_id: str, batch_size: int = 100) -> int:
        Scope(channel_id=channel_id, environment="dev")
        if not 1 <= batch_size <= 500:
            raise ValueError("清理批量必须在 1 到 500 之间")
        token = maintenance_mode.set(True)
        try:
            await self.prepare_channel(channel_id, batch_size)
            table = metadata.tables["deletion_work_items"]
            async with self.engine.connect() as connection:
                result = await connection.execute(
                    select(table)
                    .where(
                        table.c.channel_id == channel_id,
                        table.c.state != "COMPLETED",
                        table.c.next_attempt_at <= utcnow(),
                    )
                    .order_by(table.c.created_at, table.c.id)
                    .limit(batch_size)
                )
                items = [dict(row) for row in result.mappings()]
            for item in items:
                await self.execute_item(item)
            await self.finish(channel_id)
            return len(items)
        finally:
            maintenance_mode.reset(token)

    async def resolve_jobs(self, context: AuthContext, identifier: str) -> list[dict[str, Any]]:
        async with self.engine.connect() as connection:
            jobs = await rows(connection, context.scope.channel_id, "deletion_jobs", id=identifier)
            if not jobs:
                memory = await rows(
                    connection, context.scope.channel_id, "memory_deletion_jobs", id=identifier
                )
                if memory:
                    if not memory[0]["memory_ids"]:
                        jobs.append(memory[0])
                    jobs.extend(
                        await related_rows(
                            connection,
                            context.scope.channel_id,
                            "deletion_jobs",
                            "marker_id",
                            [
                                digest([scope_of(memory[0]).model_dump(), "memory", mid])
                                for mid in memory[0]["memory_ids"]
                            ],
                        )
                    )
        if not jobs:
            raise ServiceError("NOT_FOUND", "清理任务尚未生成或不存在", 404)
        policy = await read_policy(self.authorization, context)
        for job in jobs:
            own = scope_of(job)
            if own.environment != context.scope.environment or (
                context.scope.subject_id and own != context.scope
            ):
                raise ServiceError("NOT_FOUND", "当前范围没有此清理任务", 404)
            permissions = await read_actions(
                self.authorization,
                context.model_copy(update={"scope": own}),
                "content",
                "scope",
                ["content:delete"],
                policy=policy,
            )
            require_action(permissions, "content:delete")
        return jobs

    async def progress(self, context: AuthContext, identifier: str) -> dict[str, Any]:
        jobs = await self.resolve_jobs(context, identifier)
        async with self.engine.connect() as connection:
            identifiers = [job["id"] for job in jobs]
            items = await related_rows(
                connection, context.scope.channel_id, "deletion_work_items", "job_id", identifiers
            )
            receipts = await related_rows(
                connection, context.scope.channel_id, "deletion_receipts", "job_id", identifiers
            )
        state = (
            "COMPLETED"
            if all(j["state"] == "COMPLETED" for j in jobs)
            else "FAILED"
            if any(j["state"] == "FAILED" for j in jobs)
            else "PENDING"
        )
        return {
            "deletion_id": identifier,
            "status": state,
            "status_label": {"COMPLETED": "已删除", "FAILED": "清理待重试", "PENDING": "清理中"}[
                state
            ],
            "completed": sum(i["state"] == "COMPLETED" for i in items),
            "total": len(items),
            "steps": [
                {
                    "label": LABELS.get(k, "内容缓存"),
                    "completed": sum(
                        i["state"] == "COMPLETED" for i in items if i["target_type"] == k
                    ),
                    "total": sum(i["target_type"] == k for i in items),
                    "failed": sum(i["state"] == "FAILED" for i in items if i["target_type"] == k),
                }
                for k in sorted({i["target_type"] for i in items})
            ],
            "proof_digests": [r["proof_digest"] for r in receipts],
        }

    async def retry(self, context: AuthContext, identifier: str) -> dict[str, Any]:
        jobs = await self.resolve_jobs(context, identifier)
        for job in jobs:
            scope = scope_of(job)
            async with transaction(self.engine, scope, [content_key(scope)]) as uow:
                for item in await rows(
                    uow.connection, scope.channel_id, "deletion_work_items", job_id=job["id"]
                ):
                    if item["state"] == "FAILED":
                        await put(
                            UnitOfWork(uow.connection, scope_of(item), uow.keys),
                            "deletion_work_items",
                            item["id"],
                            {"state": "PENDING", "next_attempt_at": utcnow(), "last_error": None},
                        )
                if job["state"] == "FAILED":
                    await put(uow, "deletion_jobs", job["id"], {"state": "PENDING"})
        return await self.progress(context, identifier)
