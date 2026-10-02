"""会话创建、修改、消息受理和文件交付服务。"""

from datetime import timedelta
from typing import Any

from creativity_service.core.context import AuthContext
from creativity_service.core.contracts import Artifact, ResultEnvelope
from creativity_service.core.database import Repository, transaction
from creativity_service.core.database.tables import metadata as core_metadata
from creativity_service.core.deletion import ContentRef, DeletionGuard
from creativity_service.core.primitives import (
    ServiceError,
    canonical_json,
    digest,
    new_id,
    unavailable,
    utcnow,
)
from creativity_service.core.versioning import validate_schema
from creativity_service.modules.conversations import repositories as repo
from creativity_service.modules.conversations.contexts import ContextService
from creativity_service.modules.conversations.deletions import DeletionOperations
from creativity_service.modules.conversations.queries import ConversationQueries
from creativity_service.modules.conversations.schemas import (
    ConversationCreate,
    ConversationRunRequest,
    ConversationView,
    MessageInput,
    MessageReceipt,
    TextPart,
    TitleInput,
)
from creativity_service.modules.runs.schemas import AdmissionReceipt, RunRequest


class ConversationService(ConversationQueries, ContextService, DeletionOperations):
    async def run_detail(
        self, context: AuthContext, conversation_id: str, run_id: str
    ) -> ResultEnvelope:
        context = await self.access(context, conversation_id)
        async with transaction(
            self.engine, context.scope, self.hooks.keys(context, conversation_id)
        ) as uow:
            await self.hooks.current(uow, context, conversation_id)
            await repo.required(
                uow.connection,
                "conversation_turns",
                context.scope,
                conversation_id=conversation_id,
                run_id=run_id,
            )
        return await self.runs.get_run(context, run_id)

    async def create(self, context: AuthContext, body: ConversationCreate) -> ConversationView:
        await self.authorization.require(context, "conversation:write", "new")
        if self.runs.resolver is None:
            raise unavailable("智能体发布解析器")
        definition = await self.runs.resolver.resolve(
            context, RunRequest(agent_code=body.agent_code, input={})
        )
        validate_schema(definition.input_schema)
        subject_name = await self.names.name(context) if self.names else None
        conversation_id = new_id("conversation")
        keys = self.hooks.keys(context, conversation_id)
        if self.retention:
            keys.extend(self.retention.keys(context))
        async with transaction(self.engine, context.scope, keys) as uow:
            version = await Repository(
                core_metadata.tables["resource_versions"], context.scope
            ).get(uow.connection, definition.version_ids[0])
            if (
                not version
                or version["resource_type"] != "agent"
                or version["resource_id"] != definition.agent_id
                or version["state"] != "PUBLISHED"
            ):
                raise ServiceError("NOT_FOUND", "当前渠道的智能体发布版本不可用", 404)
            await DeletionGuard(context.scope).check(
                uow,
                [
                    ContentRef("conversation", conversation_id),
                    ContentRef("version", version["id"]),
                    ContentRef("agent", definition.agent_id),
                ],
            )
            retention_days = (
                min(self.retention_days, await self.retention.days(uow, context))
                if self.retention
                else self.retention_days
            )
            if not 1 <= retention_days <= 90:
                raise ServiceError("RETENTION_POLICY_INVALID", "会话保存策略无效", 503)
            row = await repo.save(
                uow,
                "conversations",
                conversation_id,
                {
                    "agent_id": definition.agent_id,
                    "agent_code": body.agent_code,
                    "agent_name": definition.agent_name,
                    "subject_name": subject_name,
                    "title": body.title.strip() or "未命名会话",
                    "status": "ACTIVE",
                    "active_run_id": None,
                    "input_schema": definition.input_schema,
                    "next_sequence": 1,
                    "next_turn_sequence": 1,
                    "expires_at": utcnow() + timedelta(days=retention_days),
                },
            )
        return self.view(row, await self.actions(context, conversation_id))

    async def title(
        self, context: AuthContext, conversation_id: str, body: TitleInput
    ) -> ConversationView:
        context = await self.access(context, conversation_id, "conversation:write")
        actions = await self.actions(context, conversation_id)
        async with transaction(
            self.engine, context.scope, self.hooks.keys(context, conversation_id)
        ) as uow:
            row = await self.hooks.current(uow, context, conversation_id)
            if row["revision"] != body.revision:
                raise ServiceError("REVISION_CONFLICT", "会话已更新，请刷新后重试", 409)
            row = await repo.save(
                uow, "conversations", conversation_id, {"title": body.title.strip() or "未命名会话"}
            )
        return self.view(row, actions)

    async def archive(
        self, context: AuthContext, conversation_id: str, *, restore: bool = False
    ) -> ConversationView:
        context = await self.access(context, conversation_id, "conversation:write")
        actions = await self.actions(context, conversation_id)
        async with transaction(
            self.engine, context.scope, self.hooks.keys(context, conversation_id)
        ) as uow:
            await self.hooks.current(uow, context, conversation_id)
            row = await repo.save(
                uow,
                "conversations",
                conversation_id,
                {"status": "ACTIVE" if restore else "ARCHIVED"},
            )
        return self.view(row, actions)

    async def submit(
        self, context: AuthContext, conversation_id: str, body: MessageInput
    ) -> MessageReceipt:
        context = await self.access(context, conversation_id, "conversation:write")
        await self.authorization.require(context, "conversation:read", conversation_id)
        if not body.content.strip():
            raise ServiceError("MESSAGE_CONTENT_REQUIRED", "请填写消息正文", 422)
        for attachment in body.attachments:
            await self.artifacts.authorization.require(
                context, "artifact:download", attachment.artifact_id
            )
        async with transaction(
            self.engine, context.scope, self.hooks.keys(context, conversation_id)
        ) as uow:
            row = await self.hooks.current(uow, context, conversation_id)
        request = ConversationRunRequest(
            agent_code=body.agent_code or row["agent_code"],
            conversation_id=conversation_id,
            client_message_id=body.client_message_id,
            input=body.input if body.input is not None else {"message": body.content},
            content_parts=(TextPart(text=body.content), *body.attachments),
        )
        receipt = await self.runs.admit_run(
            context, request, digest([conversation_id, body.client_message_id])
        )
        async with transaction(
            self.engine, context.scope, self.hooks.keys(context, conversation_id)
        ) as uow:
            await self.hooks.current(uow, context, conversation_id)
            turn = await repo.required(
                uow.connection, "conversation_turns", context.scope, run_id=receipt.run_id
            )
        return MessageReceipt(
            turn_id=turn["id"],
            user_message_id=turn["user_message_id"],
            assistant_message_id=turn["assistant_message_id"],
            sequence=turn["sequence"],
            run=receipt,
        )

    async def cancel(
        self, context: AuthContext, conversation_id: str, run_id: str
    ) -> AdmissionReceipt:
        context = await self.access(context, conversation_id, "conversation:write")
        async with transaction(
            self.engine, context.scope, self.hooks.keys(context, conversation_id)
        ) as uow:
            await self.hooks.current(uow, context, conversation_id)
            await repo.required(
                uow.connection,
                "conversation_turns",
                context.scope,
                conversation_id=conversation_id,
                run_id=run_id,
            )
        return await self.runs.cancel(context, run_id)

    async def upload(
        self, context: AuthContext, conversation_id: str, name: str, content_type: str, data: bytes
    ) -> Artifact:
        context = await self.access(context, conversation_id, "conversation:write")
        async with transaction(
            self.engine, context.scope, self.hooks.keys(context, conversation_id)
        ) as uow:
            row = await self.hooks.current(uow, context, conversation_id)
            if row["status"] != "ACTIVE":
                raise ServiceError("SESSION_ARCHIVED", "会话已归档，请先恢复", 409)
        artifact = await self.artifacts.upload(
            context, name, content_type, data, [ContentRef("conversation", conversation_id)]
        )
        return artifact.model_copy(
            update={
                "download_path": self.download_path(context, conversation_id, artifact.artifact_id)
            }
        )

    @staticmethod
    def download_path(context: AuthContext, conversation_id: str, artifact_id: str) -> str:
        prefix = "admin" if context.principal_type == "management" else "api"
        return f"/{prefix}/v1/conversations/{conversation_id}/attachments/{artifact_id}/content"

    async def download(
        self, context: AuthContext, conversation_id: str, artifact_id: str
    ) -> tuple[bytes, str, str]:
        context = await self.access(context, conversation_id)
        async with transaction(
            self.engine, context.scope, self.hooks.keys(context, conversation_id)
        ) as uow:
            await self.hooks.current(uow, context, conversation_id)
            links = await Repository(core_metadata.tables["source_links"], context.scope).find(
                uow.connection,
                source_type="conversation",
                source_id=conversation_id,
                derived_type="artifact",
                derived_id=artifact_id,
            )
            messages = await repo.rows(
                uow.connection, "messages", context.scope, conversation_id=conversation_id
            )
            if not links and not any(
                p.get("artifact_id") == artifact_id for m in messages for p in m["content_parts"]
            ):
                raise ServiceError("NOT_FOUND", "会话附件不存在", 404)
        return await self.artifacts.download(context, artifact_id)

    async def export(self, context: AuthContext, conversation_id: str) -> Artifact:
        context = await self.access(context, conversation_id)
        await self.authorization.require(context, "data:export", conversation_id)
        detail = await self.detail(context, conversation_id)
        messages: list[dict[str, Any]] = []
        turns: list[dict[str, Any]] = []
        cursor = None
        while True:
            page = await self.messages(context, conversation_id, cursor=cursor, limit=200)
            messages.extend(m.model_dump(mode="json") for m in page.items)
            turns.extend(t.model_dump(mode="json") for t in page.turns)
            cursor = page.next_cursor
            if cursor is None:
                break
        artifact = await self.artifacts.upload(
            context,
            f"{detail.conversation.title[:80]}.json",
            "application/json",
            canonical_json(
                {
                    "conversation": detail.model_dump(mode="json"),
                    "messages": messages,
                    "turns": list({t["turn_id"]: t for t in turns}.values()),
                }
            ),
            [ContentRef("conversation", conversation_id)],
        )
        return artifact.model_copy(
            update={
                "download_path": self.download_path(context, conversation_id, artifact.artifact_id)
            }
        )
