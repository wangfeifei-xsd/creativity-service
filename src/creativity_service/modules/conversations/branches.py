"""分支复制已完成消息，来源边承担遗忘传播，不复制原运行的所有权。"""

from creativity_service.core.context import AuthContext
from creativity_service.core.database import Repository, transaction
from creativity_service.core.database.tables import metadata
from creativity_service.core.deletion import ContentRef, DeletionGuard
from creativity_service.core.locking import record_key
from creativity_service.core.primitives import ServiceError, digest, utcnow
from creativity_service.modules.conversations import repositories as repo
from creativity_service.modules.conversations.base import ConversationKernel
from creativity_service.modules.conversations.schemas import BranchInput, ConversationView


class BranchOperations(ConversationKernel):
    async def branch(
        self, context: AuthContext, conversation_id: str, body: BranchInput
    ) -> ConversationView:
        context = await self.access(context, conversation_id)
        await self.authorization.require(context, "conversation:write", "new")
        branch_id = digest([context.scope.model_dump(), conversation_id, body.idempotency_key])
        fingerprint = digest(body.model_dump(exclude={"idempotency_key"}))
        origin_link = digest([branch_id, "origin"])
        sources = Repository(metadata.tables["source_links"], context.scope)
        # 预读仅用于声明锁；事务内重新读取并复核，避免删除或追加与分支交错。
        async with self.engine.connect() as connection:
            candidates = await repo.rows(
                connection, "messages", context.scope, conversation_id=conversation_id
            )
        if len(candidates) > 1000:
            raise ServiceError("BRANCH_LIMIT", "会话超过一千条消息，请缩小分支来源", 422)
        keys = self.hooks.keys(context, conversation_id) + self.hooks.keys(context, branch_id)
        keys.append(record_key(context.scope.channel_id, "source_links", origin_link))
        for message in candidates:
            keys.append(
                record_key(
                    context.scope.channel_id, "source_links", digest([branch_id, message["id"]])
                )
            )
        async with transaction(self.engine, context.scope, keys) as uow:
            origin = await self.hooks.current(uow, context, conversation_id)
            existing = await repo.one(uow.connection, "conversations", context.scope, id=branch_id)
            if existing:
                link = await sources.get(uow.connection, origin_link)
                if not link or link["source_version"] != fingerprint:
                    raise ServiceError("IDEMPOTENCY_CONFLICT", "同一分支请求的内容不能改变", 409)
                await self.hooks.current(uow, context, branch_id)
                result = existing
            else:
                messages = await repo.rows(
                    uow.connection, "messages", context.scope, conversation_id=conversation_id
                )
                cutoff = next((m for m in messages if m["id"] == body.message_id), None)
                if not cutoff or cutoff["role"] != "assistant" or cutoff["status"] != "COMPLETED":
                    raise ServiceError(
                        "BRANCH_POINT_INVALID", "请选择已完成的助手回复作为分支点", 422
                    )
                selected = sorted(
                    (m for m in messages if m["sequence"] <= cutoff["sequence"]),
                    key=lambda m: m["sequence"],
                )
                if any(m["status"] in {"PENDING", "PARTIAL"} for m in selected):
                    raise ServiceError("SESSION_BUSY", "分支范围中仍有未结束消息", 409)
                if not {m["id"] for m in selected} <= {m["id"] for m in candidates}:
                    raise ServiceError("REVISION_CONFLICT", "会话已更新，请重新创建分支", 409)
                if origin["expires_at"] <= utcnow():
                    raise ServiceError("CONTENT_EXPIRED", "来源会话已过期", 410)
                guard = DeletionGuard(context.scope)
                await guard.check(uow, [ContentRef("message", m["id"]) for m in selected])
                result = await repo.save(
                    uow,
                    "conversations",
                    branch_id,
                    {
                        **{
                            k: origin[k]
                            for k in (
                                "agent_id",
                                "agent_code",
                                "agent_name",
                                "subject_name",
                                "input_schema",
                                "expires_at",
                            )
                        },
                        "title": body.title.strip() or "新分支",
                        "status": "ACTIVE",
                        "active_run_id": None,
                        "next_sequence": len(selected) + 1,
                        "next_turn_sequence": 1,
                    },
                )
                await guard.link(
                    uow,
                    origin_link,
                    ContentRef("conversation", conversation_id),
                    ContentRef("conversation", branch_id),
                    fingerprint,
                )
                for sequence, message in enumerate(selected, 1):
                    message_id = digest([branch_id, "message", message["id"]])
                    await repo.save(
                        uow,
                        "messages",
                        message_id,
                        {
                            "conversation_id": branch_id,
                            "role": message["role"],
                            "content_parts": message["content_parts"],
                            "status": message["status"],
                            "sequence": sequence,
                            "event_sequence": 0,
                            "turn_id": None,
                            "run_id": None,
                        },
                    )
                    # 分支拥有复制消息，但不拥有原运行；删除分支也须覆盖这些副本。
                    await self.hooks.link(
                        uow,
                        context,
                        ContentRef("conversation", branch_id),
                        ContentRef("message", message_id),
                    )
                    await guard.link(
                        uow,
                        digest([branch_id, message["id"]]),
                        ContentRef("message", message["id"]),
                        ContentRef("message", message_id),
                    )
        return self.view(result, await self.actions(context, branch_id))
