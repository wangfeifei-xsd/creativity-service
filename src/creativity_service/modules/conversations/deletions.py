"""删除标记、运行取消和清理意图原子提交；全图完成由方案 25 确认。"""

from typing import Any

from sqlalchemy import delete, select

from creativity_service.core.context import AuthContext
from creativity_service.core.database import Repository, transaction
from creativity_service.core.database.tables import metadata as core_metadata
from creativity_service.core.deletion import ContentRef, DeletionGuard
from creativity_service.core.locking import record_key
from creativity_service.core.primitives import ServiceError, digest
from creativity_service.modules.conversations import repositories as repo
from creativity_service.modules.conversations.base import ConversationKernel
from creativity_service.modules.conversations.schemas import DeletionImpact, DeletionView
from creativity_service.modules.conversations.tables import metadata
from creativity_service.modules.runs import repositories as run_repo
from creativity_service.modules.runs.schemas import TERMINAL


def affected_graph(links: list[dict[str, Any]], conversation_id: str) -> list[dict[str, str]]:
    reached = {("conversation", conversation_id)}
    while True:
        expanded = reached | {
            (link["derived_type"], link["derived_id"])
            for link in links
            if (link["source_type"], link["source_id"]) in reached
        }
        if expanded == reached:
            break
        if len(expanded) > 10000:
            raise ServiceError("SOURCE_GRAPH_LIMIT", "删除影响过多，请先核查来源关系", 503)
        reached = expanded
    resources = []
    for kind, identifier in sorted(reached):
        independent = any(
            (link["derived_type"], link["derived_id"]) == (kind, identifier)
            and (link["source_type"], link["source_id"]) not in reached
            for link in links
        )
        resources.append(
            {
                "resource_type": kind,
                "resource_id": identifier,
                "mode": "UPDATE_SOURCES" if kind == "memory" and independent else "DELETE",
            }
        )
    return resources


class DeletionOperations(ConversationKernel):
    async def clean_derived(self, context: AuthContext, ref: ContentRef) -> None:
        table_name = {
            "message": "messages",
            "summary": "conversation_summaries",
            "context": "context_snapshots",
        }.get(ref.resource_type)
        if table_name is None:
            raise ValueError("不支持的会话派生来源")
        table = metadata.tables[table_name]
        async with self.engine.connect() as connection:
            row = (
                (
                    await connection.execute(
                        select(table).where(
                            table.c.channel_id == context.scope.channel_id,
                            table.c.id == ref.resource_id,
                        )
                    )
                )
                .mappings()
                .one_or_none()
            )
        if row is None:
            await self.authorization.require(context, "content:cleanup", "scope")
            return
        context = self.row_context(context, dict(row))
        await self.authorization.require(context, "content:cleanup", row["conversation_id"])
        async with transaction(
            self.engine, context.scope, self.hooks.keys(context, row["conversation_id"])
        ) as uow:
            try:
                await DeletionGuard(context.scope).check(uow, [ref])
            except ServiceError as exc:
                if exc.code != "CONTENT_DELETED":
                    raise
            else:
                raise ServiceError("DELETION_MARKER_REQUIRED", "清理前必须登记删除标记", 409)
            await uow.connection.execute(
                delete(table).where(
                    table.c.channel_id == context.scope.channel_id, table.c.id == ref.resource_id
                )
            )

    async def preview_delete(self, context: AuthContext, conversation_id: str) -> DeletionImpact:
        context = await self.access(context, conversation_id, "content:delete")
        async with transaction(
            self.engine, context.scope, self.hooks.keys(context, conversation_id)
        ) as uow:
            await self.hooks.current(uow, context, conversation_id)
            links = await Repository(core_metadata.tables["source_links"], context.scope).find(
                uow.connection
            )
            affected = affected_graph(links, conversation_id)
        return DeletionImpact(
            messages=sum(r["resource_type"] == "message" for r in affected),
            summaries=sum(r["resource_type"] == "summary" for r in affected),
            runs=sum(r["resource_type"] == "run" for r in affected),
            exclusive_memories=sum(
                r["resource_type"] == "memory" and r["mode"] == "DELETE" for r in affected
            ),
            shared_memories=sum(
                r["resource_type"] == "memory" and r["mode"] == "UPDATE_SOURCES" for r in affected
            ),
            explanation="删除后立即停止访问并取消在途任务；仅依赖本会话的记忆将撤销，有独立有效来源的记忆仅更新来源。",
        )

    @staticmethod
    def deletion_view(row: dict[str, Any]) -> DeletionView:
        return DeletionView(
            deletion_id=row["id"],
            conversation_id=row["conversation_id"],
            status=row["state"],
            status_label={
                "PENDING": "等待清理",
                "WAITING_PROPAGATION": "等待关联数据清理",
                "COMPLETED": "已删除",
                "FAILED": "清理待重试",
            }.get(row["state"], "清理中"),
            requested_at=row["created_at"],
            completed_at=row["completed_at"],
        )

    async def delete(self, context: AuthContext, conversation_id: str) -> DeletionView:
        context = await self.access(context, conversation_id, "content:delete")
        scope = context.scope
        marker_id = digest([scope.model_dump(), "conversation", conversation_id])
        job_id = digest([scope.model_dump(), "conversation-deletion", conversation_id])
        for _ in range(5):
            async with self.engine.connect() as connection:
                runs = await run_repo.rows(
                    connection,
                    "runs",
                    scope.channel_id,
                    **scope.model_dump(exclude={"channel_id"}),
                    conversation_id=conversation_id,
                )
            keys = [
                *self.hooks.keys(context, conversation_id),
                record_key(scope.channel_id, "deletion_markers", marker_id),
            ]
            for run in runs:
                keys.extend(self.runs.keys(context, run["id"], conversation_id))
            async with transaction(self.engine, scope, keys) as uow:
                row = await repo.required(
                    uow.connection, "conversations", scope, id=conversation_id
                )
                existing = await repo.one(uow.connection, "deletion_jobs", scope, id=job_id)
                if existing:
                    return self.deletion_view(existing)
                current_runs = await run_repo.rows(
                    uow.connection,
                    "runs",
                    scope.channel_id,
                    **scope.model_dump(exclude={"channel_id"}),
                    conversation_id=conversation_id,
                )
                if {r["id"] for r in current_runs} != {r["id"] for r in runs}:
                    continue
                links = await Repository(core_metadata.tables["source_links"], scope).find(
                    uow.connection
                )
                markers = Repository(core_metadata.tables["deletion_markers"], scope)
                if await markers.get(uow.connection, marker_id) is None:
                    await markers.add(
                        uow,
                        marker_id,
                        {
                            "target_type": "conversation",
                            "target_id": conversation_id,
                            "reason_code": "CONVERSATION_DELETED",
                            "requested_by": context.principal_id,
                        },
                    )
                await repo.save(uow, "conversations", row["id"], {"status": "DELETING"})
                # 标记与取消请求同事务生效，不依赖另一次授权或队列连接才能阻断后续步骤。
                for run in current_runs:
                    if run["state"] == "QUEUED":
                        await self.runs.transition(uow, run, "CANCELLED")
                    elif run["state"] == "RUNNING":
                        await self.runs.transition(uow, run, "CANCEL_REQUESTED")
                job = await repo.save(
                    uow,
                    "deletion_jobs",
                    job_id,
                    {
                        "conversation_id": conversation_id,
                        "marker_id": marker_id,
                        "scope_description": {
                            "resource_type": "conversation",
                            "resource_id": conversation_id,
                        },
                        "affected_resources": affected_graph(links, conversation_id),
                        "state": "PENDING",
                        "completed_at": None,
                    },
                )
            for run in current_runs:
                if run["state"] in TERMINAL:
                    await self.runs.after_commit(run)
            return self.deletion_view(job)
        raise ServiceError("SESSION_BUSY", "会话正在更新，请重试删除", 409)

    async def deletion(self, context: AuthContext, deletion_id: str) -> DeletionView:
        async with self.engine.connect() as connection:
            # 管理员可定位当前工作区主体的删除任务，随后恢复范围并重新授权。
            table = metadata.tables["deletion_jobs"]
            row = (
                (
                    await connection.execute(
                        select(table).where(
                            table.c.channel_id == context.scope.channel_id,
                            table.c.id == deletion_id,
                        )
                    )
                )
                .mappings()
                .one_or_none()
            )
        if row is None:
            raise ServiceError("NOT_FOUND", "删除任务不存在", 404)
        scoped = self.row_context(context, dict(row))
        await self.authorization.require(scoped, "content:delete", row["conversation_id"])
        return self.deletion_view(dict(row))

    async def clean(self, context: AuthContext, ref: ContentRef) -> None:
        context = await self.access(context, ref.resource_id, "content:cleanup")
        if ref.resource_type != "conversation":
            raise ValueError("会话清理只接受会话来源")
        async with transaction(
            self.engine, context.scope, self.hooks.keys(context, ref.resource_id)
        ) as uow:
            try:
                await DeletionGuard(context.scope).check(uow, [ref])
            except ServiceError as exc:
                if exc.code != "CONTENT_DELETED":
                    raise
            else:
                raise ServiceError("DELETION_MARKER_REQUIRED", "清理前必须登记删除标记", 409)
            runs = await run_repo.rows(
                uow.connection, "runs", context.scope.channel_id, conversation_id=ref.resource_id
            )
            if any(run["state"] not in TERMINAL for run in runs):
                raise ServiceError("DELETION_RUN_PENDING", "正在等待关联运行终结", 409)
            for name in (
                "messages",
                "conversation_turns",
                "conversation_summaries",
                "context_snapshots",
            ):
                table = metadata.tables[name]
                await uow.connection.execute(
                    delete(table).where(
                        table.c.channel_id == context.scope.channel_id,
                        table.c.conversation_id == ref.resource_id,
                    )
                )
            await repo.save(
                uow,
                "conversations",
                ref.resource_id,
                {
                    "title": "已删除会话",
                    "subject_name": None,
                    "active_run_id": None,
                    "input_schema": {},
                },
            )
            for job in await repo.rows(
                uow.connection, "deletion_jobs", context.scope, conversation_id=ref.resource_id
            ):
                if job["state"] != "COMPLETED":
                    await repo.save(
                        uow, "deletion_jobs", job["id"], {"state": "WAITING_PROPAGATION"}
                    )
