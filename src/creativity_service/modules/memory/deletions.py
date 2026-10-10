"""先原子阻断检索并清除可读内容，再由方案 25 消费持久化清理意图。"""

from typing import Any

from sqlalchemy import delete, insert, select

from creativity_service.core.context import AuthContext
from creativity_service.core.database import (
    Repository,
    UnitOfWork,
    scope_values,
    transaction,
    validate_row,
)
from creativity_service.core.database.soft_delete import active_rows
from creativity_service.core.database.tables import metadata as core_metadata
from creativity_service.core.deletion import ContentRef, DeletionGuard, content_key
from creativity_service.core.deletion.ledger import DeletionLedger
from creativity_service.core.primitives import ServiceError, digest, new_id, utcnow
from creativity_service.modules.iam.reading import (
    read_actions,
    read_policy,
    require_action,
    resource_state,
)
from creativity_service.modules.memory import repositories as repo
from creativity_service.modules.memory.base import MemoryKernel
from creativity_service.modules.memory.schemas import MemoryDeletion
from creativity_service.modules.memory.tables import metadata


class MemoryDeletions(MemoryKernel):
    @staticmethod
    def deletion_view(row: dict[str, Any]) -> MemoryDeletion:
        return MemoryDeletion(
            deletion_id=row["id"],
            status=row["state"],
            status_label={
                "PENDING": "等待清理",
                "WAITING_PROPAGATION": "等待关联数据清理",
                "COMPLETED": "已清除",
                "FAILED": "清理待重试",
            }[row["state"]],
            count=len(row["memory_ids"]),
            requested_at=row["created_at"],
            completed_at=row["completed_at"],
        )

    async def forget_in(
        self,
        uow: UnitOfWork,
        context: AuthContext,
        rows: list[dict[str, Any]],
        *,
        clear: bool = False,
    ) -> MemoryDeletion:
        uow.require_lock(content_key(context.scope))
        ids = sorted(r["id"] for r in rows)
        # 每次清空都推进阻断时间，空范围的重复清空也不能复用旧时间边界。
        job_id = (
            new_id("memory_delete")
            if clear
            else digest([context.scope.model_dump(), "memory-deletion", ids])
        )
        job = await repo.one(uow.connection, "memory_deletion_jobs", context.scope, id=job_id)
        if job:
            return self.deletion_view(job)
        table = core_metadata.tables["deletion_markers"]
        for row in rows:
            marker_id = digest([context.scope.model_dump(), "memory", row["id"]])
            if not await Repository(table, context.scope).get(uow.connection, marker_id):
                marker = {
                    "is_deleted": False,
                    **scope_values(table, context.scope),
                    "id": marker_id,
                    "created_at": utcnow(),
                    "updated_at": utcnow(),
                    "revision": 1,
                    "target_type": "memory",
                    "target_id": row["id"],
                    "reason_code": "MEMORY_FORGOTTEN",
                    "requested_by": context.principal_id,
                }
                validate_row(table, marker)
                await uow.connection.execute(insert(table).values(**marker))
            await self.version(
                uow,
                context,
                row,
                "FORGOTTEN",
                status="REVOKED",
                value=None,
                subject_name=None,
                is_deleted=True,
            )
            await self.scrub_versions(uow, context, row["id"])
        job = await repo.save(
            uow,
            "memory_deletion_jobs",
            job_id,
            {
                "memory_ids": ids,
                "kind": "CLEAR" if clear else "SINGLE",
                "state": "PENDING" if ids else "COMPLETED",
                "completed_at": None if ids else utcnow(),
            },
        )
        return self.deletion_view(job)

    async def forget(self, context: AuthContext, memory_id: str) -> MemoryDeletion:
        context = await self.locate(context, memory_id, include_deleted=True)
        async with self.engine.connect() as connection:
            current = await repo.required(
                connection, "memories", context.scope, id=memory_id, include_deleted=True
            )
        permissions = await read_actions(
            self.authorization,
            context,
            "memory",
            memory_id,
            ["memory:delete"],
            policy=await read_policy(self.authorization, context),
            state=resource_state(context, "memory", current),
        )
        require_action(permissions, "memory:delete")
        await DeletionLedger().record(
            context.scope, "memory", memory_id, "MEMORY_FORGOTTEN", context.principal_id
        )
        async with transaction(self.engine, context.scope, repo.keys(context.scope)) as uow:
            row = await repo.required(
                uow.connection, "memories", context.scope, id=memory_id, include_deleted=True
            )
            return await self.forget_in(uow, context, [row])

    async def clear(self, context: AuthContext, anchor_id: str | None = None) -> MemoryDeletion:
        context = await self.subject(context, anchor_id)
        await self.authorization.require(context, "memory:delete", "scope")
        for _ in range(10):
            async with self.engine.connect() as connection:
                rows = await repo.rows(connection, "memories", context.scope)
            for row in rows:
                await DeletionLedger().record(
                    context.scope, "memory", row["id"], "MEMORY_FORGOTTEN", context.principal_id
                )
            async with transaction(self.engine, context.scope, repo.keys(context.scope)) as uow:
                current = await repo.rows(uow.connection, "memories", context.scope)
                if {r["id"] for r in current} != {r["id"] for r in rows}:
                    continue
                return await self.forget_in(uow, context, current, clear=True)
        raise ServiceError("MEMORY_BUSY", "记忆正在更新，请重试清空", 409)

    async def deletion(self, context: AuthContext, deletion_id: str) -> MemoryDeletion:
        table = metadata.tables["memory_deletion_jobs"]
        async with self.engine.connect() as connection:
            row = (
                (
                    await connection.execute(
                        active_rows(
                            select(table).where(
                                table.c.channel_id == context.scope.channel_id,
                                table.c.id == deletion_id,
                            )
                        )
                    )
                )
                .mappings()
                .one_or_none()
            )
        if row is None:
            raise ServiceError("NOT_FOUND", "清理任务不存在", 404)
        context = self.row_context(context, dict(row))
        await self.authorization.require(context, "memory:delete", "scope")
        return self.deletion_view(dict(row))

    async def clean(self, context: AuthContext, ref: ContentRef) -> None:
        if ref.resource_type != "memory":
            raise ValueError("记忆处理器仅接受记忆引用")
        context = await self.locate(context, ref.resource_id, include_deleted=True)
        policy = await read_policy(self.authorization, context)
        if policy is None:
            await self.authorization.require(context, "content:cleanup", ref.resource_id)
        else:
            async with self.engine.connect() as connection:
                current = await repo.required(
                    connection,
                    "memories",
                    context.scope,
                    id=ref.resource_id,
                    include_deleted=True,
                )
            # 清理使用已定位的历史归属复核权限，普通详情仍不能读取删除记录。
            require_action(
                policy.actions(
                    "memory", ref.resource_id, resource_state(context, "memory", current)
                ),
                "content:cleanup",
            )
        async with transaction(self.engine, context.scope, repo.keys(context.scope)) as uow:
            row = await repo.required(
                uow.connection, "memories", context.scope, id=ref.resource_id, include_deleted=True
            )
            if not row["is_deleted"]:
                row = await self.refresh(uow, context, row)
            if row["status"] != "REVOKED":
                # 来源传播也经过此入口；独立依据仍有效时仅重算，不删除有效记忆。
                return
            await self.scrub_versions(uow, context, row["id"])
            for table, predicate in (
                (
                    metadata.tables["memory_sources"],
                    metadata.tables["memory_sources"].c.memory_id == row["id"],
                ),
                (
                    core_metadata.tables["source_links"],
                    (core_metadata.tables["source_links"].c.derived_type == "memory")
                    & (core_metadata.tables["source_links"].c.derived_id == row["id"]),
                ),
            ):
                await uow.connection.execute(
                    delete(table).where(Repository(table, context.scope).predicate(), predicate)
                )
            for job in await repo.rows(
                uow.connection, "memory_deletion_jobs", context.scope, state="PENDING"
            ):
                remaining = []
                for memory_id in job["memory_ids"]:
                    remaining.extend(
                        await repo.rows(
                            uow.connection, "memory_sources", context.scope, memory_id=memory_id
                        )
                    )
                if not remaining:
                    await repo.save(
                        uow, "memory_deletion_jobs", job["id"], {"state": "WAITING_PROPAGATION"}
                    )

    async def reconcile_source(self, context: AuthContext, ref: ContentRef) -> None:
        self.require_subject(context)
        await self.authorization.require(context, "content:cleanup", "scope")
        async with transaction(self.engine, context.scope, repo.keys(context.scope)) as uow:
            try:
                await DeletionGuard(context.scope).check(uow, [ref])
            except ServiceError as exc:
                if exc.code != "CONTENT_DELETED":
                    raise
            else:
                raise ServiceError("DELETION_MARKER_REQUIRED", "来源清理前必须登记删除标记", 409)
            for row in await repo.rows(uow.connection, "memories", context.scope):
                await self.refresh(uow, context, row)
