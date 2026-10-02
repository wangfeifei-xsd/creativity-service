"""会话范围恢复、实时动作权限和人类可读展示。"""

from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncEngine

from creativity_service.core.artifacts import ArtifactService
from creativity_service.core.context import AuthContext, Authorization, Scope
from creativity_service.core.contracts import VisibleAction
from creativity_service.core.primitives import ServiceError
from creativity_service.modules.conversations.hooks import ConversationHooks
from creativity_service.modules.conversations.ports import (
    AgentDirectory,
    RetentionReader,
    SubjectNames,
    SummaryGenerator,
)
from creativity_service.modules.conversations.schemas import STATES, ConversationView
from creativity_service.modules.conversations.tables import metadata
from creativity_service.modules.runs.repositories import verify_scope
from creativity_service.modules.runs.services import RunService


class ConversationKernel:
    def __init__(
        self,
        engine: AsyncEngine,
        authorization: Authorization,
        runs: RunService,
        artifacts: ArtifactService,
        *,
        directory: AgentDirectory | None = None,
        names: SubjectNames | None = None,
        generator: SummaryGenerator | None = None,
        retention_days: int = 90,
        retention: RetentionReader | None = None,
    ) -> None:
        if not 1 <= retention_days <= 90:
            raise ValueError("会话保留期必须在一至九十天内")
        self.engine, self.authorization, self.runs, self.artifacts = (
            engine,
            authorization,
            runs,
            artifacts,
        )
        self.directory, self.names, self.generator, self.retention_days = (
            directory,
            names,
            generator,
            retention_days,
        )
        self.hooks = ConversationHooks()
        self.retention = retention

    def row_context(self, context: AuthContext, row: dict[str, Any]) -> AuthContext:
        scope = context.scope
        if context.principal_type == "management" and scope.subject_id is None:
            if (row["channel_id"], row["environment"], row["data_scope_id"]) != (
                scope.channel_id,
                scope.environment,
                scope.data_scope_id,
            ):
                raise ServiceError("NOT_FOUND", "会话记录不存在", 404)
            return context.model_copy(
                update={"scope": Scope.model_validate({k: row[k] for k in Scope.model_fields})}
            )
        verify_scope(row, scope)
        return context

    async def access(
        self, context: AuthContext, conversation_id: str, action: str = "conversation:read"
    ) -> AuthContext:
        table = metadata.tables["conversations"]
        async with self.engine.connect() as connection:
            rows = (
                (
                    await connection.execute(
                        select(table).where(
                            table.c.channel_id == context.scope.channel_id,
                            table.c.id == conversation_id,
                        )
                    )
                )
                .mappings()
                .all()
            )
        if len(rows) != 1:
            raise ServiceError("NOT_FOUND", "会话记录不存在", 404)
        context = self.row_context(context, dict(rows[0]))
        await self.authorization.require(context, action, conversation_id)
        return context

    async def allowed(self, context: AuthContext, action: str, resource_id: str) -> bool:
        try:
            await self.authorization.require(context, action, resource_id)
        except ServiceError as exc:
            if exc.status in {403, 404}:
                return False
            raise
        return True

    async def actions(self, context: AuthContext, conversation_id: str) -> list[VisibleAction]:
        actions = []
        for key, label, permission in (
            ("title", "修改标题", "conversation:write"),
            ("archive", "归档", "conversation:write"),
            ("restore", "恢复", "conversation:write"),
            ("send", "发送", "conversation:write"),
            ("delete", "删除", "content:delete"),
            ("export", "导出", "data:export"),
            ("upload", "上传附件", "artifact:upload"),
        ):
            if await self.allowed(
                context, permission, "new" if key == "upload" else conversation_id
            ):
                actions.append(VisibleAction(action_key=key, label=label))
        return actions

    @staticmethod
    def view(row: dict[str, Any], actions: list[VisibleAction]) -> ConversationView:
        excluded = {"restore"} if row["status"] == "ACTIVE" else {"archive", "send", "upload"}
        if row["active_run_id"]:
            excluded.add("send")
        return ConversationView(
            conversation_id=row["id"],
            title=row["title"],
            agent_name=row["agent_name"],
            agent_id=row["agent_id"],
            subject_name=row["subject_name"],
            environment=row["environment"],
            environment_label={"dev": "开发", "test": "测试", "fat": "验收", "prod": "生产"}[
                row["environment"]
            ],
            status=row["status"],
            status_label=STATES[row["status"]],
            revision=row["revision"],
            active_run_id=row["active_run_id"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
            expires_at=row["expires_at"],
            actions=[a for a in actions if a.action_key not in excluded],
        )
