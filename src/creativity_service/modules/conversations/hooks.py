"""轮次与运行受理、片段投影、终态释放共用调用方事务。"""

from typing import Any

from jsonschema import Draft202012Validator
from sqlalchemy import insert

from creativity_service.core.context import AuthContext
from creativity_service.core.database import Repository, UnitOfWork, scope_values, validate_row
from creativity_service.core.database.tables import metadata as core_metadata
from creativity_service.core.deletion import ContentRef, DeletionGuard, content_key
from creativity_service.core.locking import ResourceKey
from creativity_service.core.primitives import RunInput, ServiceError, digest, new_id, utcnow
from creativity_service.modules.conversations import repositories as repo
from creativity_service.modules.conversations.schemas import ConversationRunRequest
from creativity_service.modules.runs import repositories as run_repo
from creativity_service.modules.runs.schemas import TERMINAL, ResolvedDefinition


def compatible_schema(previous: dict[str, Any], current: dict[str, Any]) -> bool:
    """仅接受能证明兼容的契约放宽；无法证明时要求新会话，避免错误复用历史。"""
    if previous == current or not current:
        return True
    ignored = {"title", "description", "$comment", "examples", "default"}
    before = {k: v for k, v in previous.items() if k not in ignored}
    after = {k: v for k, v in current.items() if k not in ignored}
    if before == after:
        return True
    if before.get("type") != "object" or after.get("type") != "object":
        return False
    if not set(after.get("required", [])) <= set(before.get("required", [])):
        return False
    special = {"properties", "required", "additionalProperties"}
    if {k: v for k, v in before.items() if k not in special} != {
        k: v for k, v in after.items() if k not in special
    }:
        return False
    if before.get("additionalProperties", True) and after.get("additionalProperties") is False:
        return False
    old_props, new_props = before.get("properties", {}), after.get("properties", {})
    for key, schema in old_props.items():
        if key not in new_props:
            if after.get("additionalProperties", True) is not True:
                return False
        elif not compatible_schema(schema, new_props[key]):
            return False
    # 原契约允许任意扩展字段时，新增字段的类型约束也可能收紧历史输入。
    return not (before.get("additionalProperties", True) and set(new_props) - set(old_props))


def request_parts(request: RunInput) -> list[dict[str, Any]]:
    parts = getattr(request, "content_parts", ())
    if parts:
        return [part.model_dump(mode="json") for part in parts]
    content = request.input.get("message")
    if not isinstance(content, str) or not content.strip():
        raise ServiceError("MESSAGE_CONTENT_REQUIRED", "会话运行须提供消息正文", 422)
    return [{"type": "text", "text": content}]


def message_digest(request: RunInput) -> str:
    return digest(
        {
            "agent_code": request.agent_code,
            "input": request.input,
            "content_parts": request_parts(request),
        }
    )


class ConversationHooks:
    async def rerun_request(
        self,
        uow: UnitOfWork,
        context: AuthContext,
        run: dict[str, Any],
        request: RunInput,
        key: str,
    ) -> RunInput:
        turn = await repo.required(
            uow.connection, "conversation_turns", context.scope, run_id=run["id"]
        )
        message = await repo.required(
            uow.connection, "messages", context.scope, id=turn["user_message_id"]
        )
        return ConversationRunRequest.model_validate(
            {
                **request.model_dump(),
                "client_message_id": digest(["rerun", run["id"], key]),
                "content_parts": message["content_parts"],
            }
        )

    def keys(self, context: AuthContext, conversation_id: str) -> list[ResourceKey]:
        return [
            run_repo.conversation_key(context.scope, conversation_id),
            content_key(context.scope),
        ]

    async def current(
        self, uow: UnitOfWork, context: AuthContext, conversation_id: str
    ) -> dict[str, Any]:
        uow.require_scope(context.scope)
        uow.require_lock(run_repo.conversation_key(context.scope, conversation_id))
        row = await repo.required(
            uow.connection, "conversations", context.scope, id=conversation_id
        )
        if row["status"] in {"DELETING", "DELETED"} or row["expires_at"] <= utcnow():
            raise ServiceError("NOT_FOUND", "会话不存在或已删除", 404)
        await DeletionGuard(context.scope).check(uow, [ContentRef("conversation", conversation_id)])
        return row

    async def replay(self, uow: UnitOfWork, context: AuthContext, request: RunInput) -> str | None:
        assert request.conversation_id is not None
        row = await self.current(uow, context, request.conversation_id)
        if row["agent_code"] != request.agent_code:
            raise ServiceError("SESSION_AGENT_MISMATCH", "会话不能关联其他智能体", 409)
        client_message_id = getattr(request, "client_message_id", None)
        if not client_message_id:
            raise ServiceError("CLIENT_MESSAGE_ID_REQUIRED", "请提供客户端消息标识", 422)
        turn = await repo.one(
            uow.connection,
            "conversation_turns",
            context.scope,
            conversation_id=row["id"],
            client_message_id=client_message_id,
        )
        if turn:
            if turn["request_digest"] != message_digest(request):
                raise ServiceError("IDEMPOTENCY_CONFLICT", "相同消息标识的正文或输入不同", 409)
            return str(turn["run_id"])
        if row["status"] == "ARCHIVED":
            raise ServiceError("SESSION_ARCHIVED", "会话已归档，请先恢复", 409)
        if row["active_run_id"]:
            raise ServiceError("SESSION_BUSY", "会话正在处理上一条消息", 409)
        return None

    async def prepare(
        self,
        uow: UnitOfWork,
        context: AuthContext,
        definition: ResolvedDefinition,
        request: RunInput,
    ) -> None:
        assert request.conversation_id is not None
        row = await self.current(uow, context, request.conversation_id)
        if definition.agent_id != row["agent_id"] or request.agent_code != row["agent_code"]:
            raise ServiceError("SESSION_AGENT_MISMATCH", "会话不能关联其他智能体", 409)
        if not compatible_schema(row["input_schema"], definition.input_schema):
            raise ServiceError(
                "SESSION_CONTRACT_INCOMPATIBLE", "智能体输入要求已变化，请新建会话或迁移后继续", 409
            )
        turns = await repo.rows(
            uow.connection, "conversation_turns", context.scope, conversation_id=row["id"]
        )
        if any(
            not Draft202012Validator(definition.input_schema).is_valid(turn["input"])
            for turn in turns
        ):
            raise ServiceError(
                "SESSION_CONTRACT_INCOMPATIBLE", "历史输入与当前智能体不兼容，请新建会话", 409
            )
        await repo.save(uow, "conversations", row["id"], {"input_schema": definition.input_schema})

    async def link(
        self, uow: UnitOfWork, context: AuthContext, source: ContentRef, derived: ContentRef
    ) -> None:
        """来源图锁覆盖动态派生关系，预声明的全部锁不随随机消息标识增长。"""
        uow.require_lock(content_key(context.scope))
        await DeletionGuard(context.scope).check(uow, [source, derived])
        link_id = digest(
            [source.resource_type, source.resource_id, derived.resource_type, derived.resource_id]
        )
        table = core_metadata.tables["source_links"]
        links = Repository(table, context.scope)
        if await links.get(uow.connection, link_id) is None:
            row = {
                **scope_values(table, context.scope),
                "id": link_id,
                "created_at": utcnow(),
                "updated_at": utcnow(),
                "revision": 1,
                "source_type": source.resource_type,
                "source_id": source.resource_id,
                "derived_type": derived.resource_type,
                "derived_id": derived.resource_id,
                "source_version": None,
            }
            validate_row(table, row)
            await uow.connection.execute(insert(table).values(**row))

    async def admit(
        self, uow: UnitOfWork, context: AuthContext, run: dict[str, Any], request: RunInput
    ) -> None:
        conversation_id = run["conversation_id"]
        row = await self.current(uow, context, conversation_id)
        if await self.replay(uow, context, request):
            raise ServiceError("IDEMPOTENCY_CONFLICT", "消息已经受理", 409)
        if row["active_run_id"]:
            raise ServiceError("SESSION_BUSY", "会话正在处理上一条消息", 409)
        parts = request_parts(request)
        artifact_repo = Repository(core_metadata.tables["artifacts"], context.scope)
        for part in parts:
            if part["type"] == "attachment":
                artifact = await artifact_repo.get(uow.connection, part["artifact_id"])
                if (
                    not artifact
                    or artifact["state"] != "AVAILABLE"
                    or artifact["expires_at"] <= utcnow()
                ):
                    raise ServiceError("NOT_FOUND", "附件不存在或已失效", 404)
                await DeletionGuard(context.scope).check(
                    uow, [ContentRef("artifact", artifact["id"])]
                )
        turns = await repo.rows(
            uow.connection, "conversation_turns", context.scope, conversation_id=conversation_id
        )
        source_run_id, conditions = None, {}
        if turns:
            previous = max(turns, key=lambda turn: turn["sequence"])
            prior_run = await run_repo.required(
                uow.connection, "runs", context.scope.channel_id, id=previous["run_id"]
            )
            if prior_run["state"] == "SUCCEEDED" and prior_run["result_ref"]:
                result = await run_repo.one(
                    uow.connection,
                    "run_contents",
                    context.scope.channel_id,
                    id=prior_run["result_ref"],
                )
                if result and result["payload"].get("business_status") == "NEEDS_INPUT":
                    source_run_id = prior_run["id"]
                    confirmed = result["payload"].get("data", {}).get("confirmed_conditions", {})
                    conditions = confirmed if isinstance(confirmed, dict) else {}
        version = await Repository(core_metadata.tables["resource_versions"], context.scope).get(
            uow.connection, run["agent_version_id"]
        )
        snapshot = await Repository(core_metadata.tables["release_snapshots"], context.scope).get(
            uow.connection, run["release_snapshot_id"]
        )
        if not version or not snapshot:
            raise ServiceError("SNAPSHOT_INVALID", "本轮冻结版本不可用", 409)
        turn_id, user_id, assistant_id = new_id("turn"), new_id("message"), new_id("message")
        await repo.save(
            uow,
            "conversation_turns",
            turn_id,
            {
                "conversation_id": conversation_id,
                "client_message_id": getattr(request, "client_message_id", None),
                "request_digest": message_digest(request),
                "user_message_id": user_id,
                "assistant_message_id": assistant_id,
                "run_id": run["id"],
                "sequence": row["next_turn_sequence"],
                "agent_version_id": run["agent_version_id"],
                "version_label": version["version_label"],
                "input": request.input,
                "input_schema": row["input_schema"],
                "output_schema": snapshot["output_schema"],
                "source_run_id": source_run_id,
                "confirmed_conditions": conditions,
            },
        )
        for message_id, role, content, sequence in (
            (user_id, "user", parts, row["next_sequence"]),
            (assistant_id, "assistant", [], row["next_sequence"] + 1),
        ):
            await repo.save(
                uow,
                "messages",
                message_id,
                {
                    "conversation_id": conversation_id,
                    "role": role,
                    "content_parts": content,
                    "run_id": run["id"],
                    "turn_id": turn_id,
                    "status": "PENDING",
                    "sequence": sequence,
                    "event_sequence": 0,
                },
            )
            await self.link(
                uow,
                context,
                ContentRef("conversation", conversation_id),
                ContentRef("message", message_id),
            )
        for part in parts:
            if part["type"] == "attachment":
                await self.link(
                    uow,
                    context,
                    ContentRef("artifact", part["artifact_id"]),
                    ContentRef("message", user_id),
                )
        await repo.save(
            uow,
            "conversations",
            conversation_id,
            {
                "active_run_id": run["id"],
                "next_sequence": row["next_sequence"] + 2,
                "next_turn_sequence": row["next_turn_sequence"] + 1,
            },
        )

    async def project(
        self,
        uow: UnitOfWork,
        context: AuthContext,
        run: dict[str, Any],
        event_type: str,
        payload: dict[str, Any],
    ) -> None:
        await self.current(uow, context, run["conversation_id"])
        turn = await repo.required(
            uow.connection, "conversation_turns", context.scope, run_id=run["id"]
        )
        message = await repo.required(
            uow.connection, "messages", context.scope, id=turn["assistant_message_id"]
        )
        if run["event_sequence"] <= message["event_sequence"] or run["state"] in TERMINAL:
            return
        if event_type == "text_delta":
            text = payload.get("text")
            if not isinstance(text, str) or not text:
                raise ServiceError("MESSAGE_EVENT_INVALID", "文本片段不能为空", 422)
            existing = "".join(p["text"] for p in message["content_parts"] if p["type"] == "text")
            if len(existing) + len(text) > 100000:
                raise ServiceError("MESSAGE_TOO_LARGE", "助手消息长度超限", 422)
            await repo.save(
                uow,
                "messages",
                message["id"],
                {
                    "content_parts": [{"type": "text", "text": existing + text}],
                    "status": "PARTIAL",
                    "event_sequence": run["event_sequence"],
                },
            )
        elif (
            payload.get("role") in {"system", "tool"}
            and isinstance(payload.get("text"), str)
            and payload["text"]
        ):
            row = await self.current(uow, context, run["conversation_id"])
            message_id = digest([run["id"], "message-event", run["event_sequence"]])
            if await repo.one(uow.connection, "messages", context.scope, id=message_id):
                return
            await repo.save(
                uow,
                "messages",
                message_id,
                {
                    "conversation_id": row["id"],
                    "role": payload["role"],
                    "content_parts": [{"type": "text", "text": payload["text"]}],
                    "status": "PARTIAL",
                    "run_id": run["id"],
                    "turn_id": turn["id"],
                    "sequence": row["next_sequence"],
                    "event_sequence": run["event_sequence"],
                },
            )
            await repo.save(
                uow, "conversations", row["id"], {"next_sequence": row["next_sequence"] + 1}
            )
            await self.link(
                uow,
                context,
                ContentRef("conversation", row["id"]),
                ContentRef("message", message_id),
            )

    async def finish(self, uow: UnitOfWork, context: AuthContext, run: dict[str, Any]) -> None:
        if run["state"] not in TERMINAL:
            raise ServiceError("RUN_NOT_FINISHED", "运行尚未有效终结", 409)
        row = await repo.required(
            uow.connection, "conversations", context.scope, id=run["conversation_id"]
        )
        if row["status"] not in {"DELETING", "DELETED"}:
            status = {"SUCCEEDED": "COMPLETED", "CANCELLED": "CANCELLED"}.get(
                run["state"], "FAILED"
            )
            for message in await repo.rows(
                uow.connection, "messages", context.scope, run_id=run["id"]
            ):
                if message["status"] != status:
                    await repo.save(uow, "messages", message["id"], {"status": status})
        if row["active_run_id"] == run["id"]:
            await repo.save(uow, "conversations", row["id"], {"active_run_id": None})
