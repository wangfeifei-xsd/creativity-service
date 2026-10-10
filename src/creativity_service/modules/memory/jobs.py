"""后台整理进度与显式重试，只返回当前授权主体的任务引用。"""

from sqlalchemy import select

from creativity_service.core.context import AuthContext
from creativity_service.core.database import transaction
from creativity_service.core.database.soft_delete import active_rows
from creativity_service.core.primitives import ServiceError, utcnow
from creativity_service.modules.conversations.tables import metadata as conversations
from creativity_service.modules.iam.reading import (
    read_actions,
    read_policy,
    require_action,
    resource_state,
)
from creativity_service.modules.memory import repositories as repo
from creativity_service.modules.memory.base import MemoryKernel
from creativity_service.modules.memory.reading import indexed, scoped_id, scoped_rows
from creativity_service.modules.memory.schemas import ConsolidationView, MemoryConfirm
from creativity_service.modules.memory.tables import metadata
from creativity_service.modules.runs.tables import metadata as runs

LABELS = {
    "PENDING": "等待整理",
    "ADMITTED": "整理中",
    "COMPLETED": "已完成",
    "FAILED": "整理失败",
    "SKIPPED": "已停止",
}


class MemoryJobs(MemoryKernel):
    async def consolidation_jobs(
        self, context: AuthContext, anchor_id: str | None = None
    ) -> list[ConsolidationView]:
        if anchor_id:
            context = await self.subject(context, anchor_id)
        policy = await read_policy(self.authorization, context)
        permissions = await read_actions(
            self.authorization, context, "memory", "scope", ["memory:read"], policy=policy
        )
        require_action(permissions, "memory:read")
        scope = context.scope.model_dump()
        if context.principal_type == "management" and not context.scope.subject_id:
            scope.pop("subject_id")
            scope.pop("subject_type")
        table = metadata.tables["memory_consolidations"]
        async with self.engine.connect() as connection:
            rows = [
                dict(v)
                for v in (
                    await connection.execute(
                        active_rows(
                            select(table)
                            .where(*(table.c[k] == v for k, v in scope.items()))
                            .order_by(table.c.created_at.desc())
                            .limit(100)
                        )
                    )
                ).mappings()
            ]
        contexts = {scoped_id(row): self.row_context(context, row) for row in rows}
        async with self.engine.connect() as connection:
            conversation_rows = indexed(
                await scoped_rows(
                    connection,
                    conversations.tables["conversations"],
                    [(contexts[scoped_id(r)], r["conversation_id"]) for r in rows],
                )
            )
            run_rows = indexed(
                await scoped_rows(
                    connection,
                    runs.tables["runs"],
                    [
                        (contexts[scoped_id(r)], r["generation_run_id"])
                        for r in rows
                        if r["generation_run_id"]
                    ],
                )
            )
        result = []
        for row in rows:
            scoped = contexts[scoped_id(row)]
            permissions = await read_actions(
                self.authorization,
                scoped,
                "memory",
                "scope",
                ["memory:read", "memory:write"],
                policy=policy,
            )
            if "memory:read" not in permissions:
                continue
            title = "会话名称不可用"
            conversation = conversation_rows.get(scoped_id(row, row["conversation_id"]))
            if conversation and conversation["status"] not in {"DELETING", "DELETED"}:
                allowed = await read_actions(
                    self.authorization,
                    scoped,
                    "conversation",
                    conversation["id"],
                    ["conversation:read"],
                    policy=policy,
                    state=resource_state(scoped, "conversation", conversation),
                )
                if "conversation:read" in allowed:
                    title = conversation["title"]
            run_id = row["generation_run_id"]
            run = run_rows.get(scoped_id(row, run_id)) if run_id else None
            run_allowed = (
                await read_actions(
                    self.authorization,
                    scoped,
                    "run",
                    run_id,
                    ["run:read"],
                    policy=policy,
                    state=resource_state(scoped, "run", run),
                )
                if run
                else frozenset()
            )
            result.append(
                ConsolidationView(
                    id=row["id"],
                    revision=row["revision"],
                    conversation_name=title,
                    state=row["state"],
                    state_label=LABELS[row["state"]],
                    attempt=row["attempt"],
                    generated_count=len(row["memory_ids"]),
                    generation_run_id=run_id if run_id and "run:read" in run_allowed else None,
                    created_at=row["created_at"],
                    updated_at=row["updated_at"],
                    message=(
                        "模型不支持后台整理所需的能力，请核对原生结构化输出与模型路由"
                        if row["error_code"] == "MODEL_CAPABILITY_UNSUPPORTED"
                        else "请检查运行详情与记忆策略"
                        if row["error_code"]
                        else None
                    ),
                    can_retry=row["state"] == "FAILED" and "memory:write" in permissions,
                )
            )
        return result

    async def retry_consolidation(
        self, context: AuthContext, identifier: str, body: MemoryConfirm
    ) -> None:
        table = metadata.tables["memory_consolidations"]
        async with self.engine.connect() as connection:
            row = (
                (
                    await connection.execute(
                        active_rows(
                            select(table).where(
                                table.c.channel_id == context.scope.channel_id,
                                table.c.id == identifier,
                            )
                        )
                    )
                )
                .mappings()
                .one_or_none()
            )
        if row is None:
            raise ServiceError("NOT_FOUND", "后台整理任务不存在", 404)
        context = self.row_context(context, dict(row))
        await self.authorization.require(context, "memory:write", "scope")
        async with transaction(self.engine, context.scope, repo.keys(context.scope)) as uow:
            current = await repo.required(
                uow.connection, "memory_consolidations", context.scope, id=identifier
            )
            if current["revision"] != body.revision or current["state"] != "FAILED":
                raise ServiceError("REVISION_CONFLICT", "后台整理状态已变化，请刷新", 409)
            await repo.save(
                uow,
                "memory_consolidations",
                identifier,
                {
                    "state": "PENDING",
                    "attempt": current["attempt"] + 1,
                    "generation_run_id": None,
                    "error_code": None,
                    "next_attempt_at": utcnow(),
                },
            )
