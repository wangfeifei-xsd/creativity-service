"""稳定游标、持久化消息时间线及每轮冻结结果。"""

import base64
import json
from datetime import datetime
from typing import Any

from sqlalchemy import and_, or_, select

from creativity_service.core.context import AuthContext
from creativity_service.core.contracts import BusinessResult, VisibleAction
from creativity_service.core.database import Repository, transaction
from creativity_service.core.database.tables import metadata as core_metadata
from creativity_service.core.deletion import ContentRef, DeletionGuard
from creativity_service.core.primitives import ServiceError, canonical_json, digest, utcnow
from creativity_service.modules.conversations import repositories as repo
from creativity_service.modules.conversations.base import ConversationKernel
from creativity_service.modules.conversations.schemas import (
    MESSAGE_STATES,
    ROLES,
    AttachmentView,
    ContextView,
    ConversationDetail,
    ConversationList,
    MessagePage,
    MessageView,
    SummaryView,
    TurnView,
)
from creativity_service.modules.conversations.tables import metadata
from creativity_service.modules.runs import repositories as run_repo
from creativity_service.modules.runs.schemas import LABELS, TERMINAL


def encode_cursor(value: dict[str, Any]) -> str:
    return base64.urlsafe_b64encode(canonical_json(value)).decode().rstrip("=")


def decode_cursor(value: str | None, binding: str) -> dict[str, Any] | None:
    if value is None:
        return None
    try:
        if len(value) > 2048:
            raise ValueError()
        parsed = json.loads(
            base64.b64decode(value + "=" * (-len(value) % 4), altchars=b"-_", validate=True)
        )
        if not isinstance(parsed, dict) or parsed.get("binding") != binding:
            raise ValueError()
        return parsed
    except (ValueError, TypeError, UnicodeError) as exc:
        raise ServiceError("CURSOR_INVALID", "分页游标已失效，请刷新列表", 422) from exc


class ConversationQueries(ConversationKernel):
    async def list_conversations(
        self,
        context: AuthContext,
        *,
        cursor: str | None = None,
        limit: int = 30,
        status: str | None = None,
        agent_id: str | None = None,
        subject: str | None = None,
        environment: str | None = None,
        start_at: datetime | None = None,
        end_at: datetime | None = None,
    ) -> ConversationList:
        if not 1 <= limit <= 200 or status not in {None, "ACTIVE", "ARCHIVED"}:
            raise ServiceError("FILTER_INVALID", "会话筛选条件不正确", 422)
        if any(t is not None and t.tzinfo is None for t in (start_at, end_at)) or (
            start_at and end_at and start_at >= end_at
        ):
            raise ServiceError("FILTER_INVALID", "请提供含时区的有效时间范围", 422)
        await self.authorization.require(context, "conversation:read", "scope")
        filters = {
            "status": status,
            "agent_id": agent_id,
            "subject": subject,
            "environment": environment,
            "start_at": start_at.isoformat() if start_at else None,
            "end_at": end_at.isoformat() if end_at else None,
        }
        binding = digest([context.scope.model_dump(), context.principal_id, filters])
        cursor_value = decode_cursor(cursor, binding)
        table = metadata.tables["conversations"]
        scope = context.scope.model_dump()
        if context.principal_type == "management" and context.scope.subject_id is None:
            scope.pop("subject_type")
            scope.pop("subject_id")
        predicates = [table.c[k] == v for k, v in scope.items()]
        predicates += [
            table.c.status.in_([status] if status else ["ACTIVE", "ARCHIVED"]),
            table.c.expires_at > utcnow(),
        ]
        if environment:
            predicates.append(table.c.environment == environment)
        if agent_id:
            predicates.append(table.c.agent_id == agent_id)
        if subject:
            predicates.append(table.c.subject_name.contains(subject, autoescape=True))
        if start_at:
            predicates.append(table.c.created_at >= start_at)
        if end_at:
            predicates.append(table.c.created_at < end_at)
        if cursor_value:
            try:
                at = datetime.fromisoformat(cursor_value["at"])
                if at.tzinfo is None or not isinstance(cursor_value["id"], str):
                    raise ValueError()
                predicates.append(
                    or_(
                        table.c.created_at < at,
                        and_(table.c.created_at == at, table.c.id < cursor_value["id"]),
                    )
                )
            except (KeyError, ValueError, TypeError) as exc:
                raise ServiceError("CURSOR_INVALID", "分页游标不正确", 422) from exc
        async with self.engine.connect() as connection:
            candidates = [
                dict(r)
                for r in (
                    await connection.execute(
                        select(table)
                        .where(*predicates)
                        .order_by(table.c.created_at.desc(), table.c.id.desc())
                        .limit(limit + 1)
                    )
                ).mappings()
            ]
        items = []
        for row in candidates[:limit]:
            item_context = self.row_context(context, row)
            if not await self.allowed(item_context, "conversation:read", row["id"]):
                continue
            actions = await self.actions(item_context, row["id"])
            try:
                async with transaction(
                    self.engine, item_context.scope, self.hooks.keys(item_context, row["id"])
                ) as uow:
                    current = await self.hooks.current(uow, item_context, row["id"])
                    items.append(self.view(current, actions))
            except ServiceError as exc:
                if exc.status not in {404, 410}:
                    raise
        more = len(candidates) > limit
        next_cursor = (
            encode_cursor(
                {
                    "binding": binding,
                    "at": candidates[limit - 1]["created_at"].isoformat(),
                    "id": candidates[limit - 1]["id"],
                }
            )
            if more
            else None
        )
        executable = self.runs.resolver is not None
        agents = await self.directory.list_available(context) if self.directory else []
        actions = (
            [VisibleAction(action_key="create", label="新建会话")]
            if executable and agents and await self.allowed(context, "conversation:write", "new")
            else []
        )
        return ConversationList(
            items=items,
            next_cursor=next_cursor,
            has_more=more,
            actions=actions,
            agents=agents,
            unavailable_reason=None if executable else "智能体运行服务暂不可用",
        )

    async def detail(self, context: AuthContext, conversation_id: str) -> ConversationDetail:
        context = await self.access(context, conversation_id)
        actions = await self.actions(context, conversation_id)
        async with transaction(
            self.engine, context.scope, self.hooks.keys(context, conversation_id)
        ) as uow:
            row = await self.hooks.current(uow, context, conversation_id)
            summaries = []
            source_rows = await repo.rows(
                uow.connection, "messages", context.scope, conversation_id=conversation_id
            )
            sequences = {m["id"]: m["sequence"] for m in source_rows}
            for summary in sorted(
                await repo.rows(
                    uow.connection,
                    "conversation_summaries",
                    context.scope,
                    conversation_id=conversation_id,
                    status="VALID",
                ),
                key=lambda s: s["version"],
            ):
                try:
                    await DeletionGuard(context.scope).check(
                        uow, [ContentRef("summary", summary["id"])]
                    )
                except ServiceError as exc:
                    if exc.code != "CONTENT_DELETED":
                        raise
                    continue
                summaries.append(
                    SummaryView(
                        summary_id=summary["id"],
                        version=summary["version"],
                        content=summary["content"],
                        source_message_ids=summary["source_message_ids"],
                        source_sequences=[
                            sequences[i] for i in summary["source_message_ids"] if i in sequences
                        ],
                        truncation=summary["truncation"],
                        created_at=summary["created_at"],
                    )
                )
            contexts = [
                ContextView(
                    run_id=s["run_id"],
                    included_message_ids=s["included_message_ids"],
                    summary_version=s["summary_version"],
                    truncation=s["truncation"],
                )
                for s in await repo.rows(
                    uow.connection,
                    "context_snapshots",
                    context.scope,
                    conversation_id=conversation_id,
                )
            ]
        return ConversationDetail(
            conversation=self.view(row, actions),
            summaries=summaries,
            contexts=contexts,
            input_schema=row["input_schema"],
            executable=self.runs.resolver is not None,
            unavailable_reason=None if self.runs.resolver else "智能体运行服务暂不可用",
        )

    async def messages(
        self,
        context: AuthContext,
        conversation_id: str,
        *,
        cursor: str | None = None,
        limit: int = 50,
    ) -> MessagePage:
        if not 1 <= limit <= 200:
            raise ServiceError("PAGE_INVALID", "分页数量超出范围", 422)
        context = await self.access(context, conversation_id)
        binding = digest(
            [context.scope.model_dump(), context.principal_id, conversation_id, "messages-v1"]
        )
        value = decode_cursor(cursor, binding)
        after, ceiling = 0, None
        if value:
            try:
                after, ceiling = value["after"], value["ceiling"]
                if type(after) is not int or type(ceiling) is not int or not 0 <= after <= ceiling:
                    raise ValueError()
            except (KeyError, TypeError, ValueError) as exc:
                raise ServiceError("CURSOR_INVALID", "历史游标不正确", 422) from exc
        async with self.engine.connect() as connection:
            all_turns = await repo.rows(
                connection, "conversation_turns", context.scope, conversation_id=conversation_id
            )
        can_write = await self.allowed(context, "conversation:write", conversation_id)
        permissions = {
            t["run_id"]: (
                can_write and await self.allowed(context, "run:create", t["run_id"]),
                await self.allowed(context, "run:read", t["run_id"])
                and await self.allowed(context, "run:content", t["run_id"]),
            )
            for t in all_turns
        }
        async with transaction(
            self.engine, context.scope, self.hooks.keys(context, conversation_id)
        ) as uow:
            row = await self.hooks.current(uow, context, conversation_id)
            ceiling = row["next_sequence"] - 1 if ceiling is None else ceiling
            table = metadata.tables["messages"]
            found = [
                dict(r)
                for r in (
                    await uow.connection.execute(
                        select(table)
                        .where(
                            table.c.channel_id == context.scope.channel_id,
                            table.c.conversation_id == conversation_id,
                            table.c.sequence > after,
                            table.c.sequence <= ceiling,
                        )
                        .order_by(table.c.sequence)
                        .limit(limit + 1)
                    )
                ).mappings()
            ]
            selected = found[:limit]
            turns, run_states = [], {}
            for turn in sorted(
                await repo.rows(
                    uow.connection,
                    "conversation_turns",
                    context.scope,
                    conversation_id=conversation_id,
                ),
                key=lambda t: t["sequence"],
            ):
                if turn["id"] not in {m["turn_id"] for m in selected}:
                    continue
                run = await run_repo.required(
                    uow.connection, "runs", context.scope.channel_id, id=turn["run_id"]
                )
                run_states[run["id"]] = run["state"]
                await self.runs.guard(uow, run)
                result = None
                if run["state"] == "SUCCEEDED" and run["result_ref"]:
                    content = await run_repo.one(
                        uow.connection,
                        "run_contents",
                        context.scope.channel_id,
                        id=run["result_ref"],
                    )
                    if content and content["payload"] is not None:
                        result = BusinessResult.model_validate(content["payload"])
                can_cancel, can_view = permissions.get(run["id"], (False, False))
                turns.append(
                    TurnView(
                        turn_id=turn["id"],
                        sequence=turn["sequence"],
                        run_id=run["id"],
                        version_label=turn["version_label"],
                        state=run["state"],
                        state_label=LABELS[run["state"]],
                        result=result,
                        result_label={
                            "COMPLETED": "已完成",
                            "NEEDS_INPUT": "需要补充信息",
                            "NO_MATCH": "暂无匹配",
                            "INSUFFICIENT_DATA": "数据不足",
                            "PARTIAL": "部分结果",
                        }.get(result.business_status)
                        if result
                        else None,
                        output_schema=turn["output_schema"],
                        source_run_id=turn["source_run_id"],
                        duration_seconds=(run["completed_at"] - run["created_at"]).total_seconds()
                        if run["completed_at"]
                        else None,
                        can_cancel=can_cancel and run["state"] not in TERMINAL,
                        can_view_run=can_view,
                    )
                )
            items = []
            for message in selected:
                await DeletionGuard(context.scope).check(
                    uow, [ContentRef("message", message["id"])]
                )
                attachments = []
                for part in message["content_parts"]:
                    if part["type"] != "attachment":
                        continue
                    artifact = await Repository(
                        core_metadata.tables["artifacts"], context.scope
                    ).get(uow.connection, part["artifact_id"])
                    if (
                        artifact
                        and artifact["state"] == "AVAILABLE"
                        and artifact["expires_at"] > utcnow()
                    ):
                        await DeletionGuard(context.scope).check(
                            uow, [ContentRef("artifact", artifact["id"])]
                        )
                        prefix = "admin" if context.principal_type == "management" else "api"
                        attachments.append(
                            AttachmentView(
                                artifact_id=artifact["id"],
                                name=artifact["name"],
                                size_bytes=artifact["size_bytes"],
                                download_path=f"/{prefix}/v1/conversations/{conversation_id}/attachments/{artifact['id']}/content",
                            )
                        )
                state = run_states.get(message["run_id"])
                status = {
                    "SUCCEEDED": "COMPLETED",
                    "CANCELLED": "CANCELLED",
                    "FAILED": "FAILED",
                    "TIMED_OUT": "FAILED",
                }.get(state or "", message["status"])
                items.append(
                    MessageView(
                        message_id=message["id"],
                        turn_id=message["turn_id"],
                        run_id=message["run_id"],
                        sequence=message["sequence"],
                        role=message["role"],
                        role_label=ROLES[message["role"]],
                        status=status,
                        status_label=MESSAGE_STATES[status],
                        text="\n".join(
                            p["text"] for p in message["content_parts"] if p["type"] == "text"
                        ),
                        attachments=attachments,
                        created_at=message["created_at"],
                    )
                )
        more = len(found) > limit
        return MessagePage(
            items=items,
            turns=turns,
            has_more=more,
            next_cursor=encode_cursor(
                {"binding": binding, "after": selected[-1]["sequence"], "ceiling": ceiling}
            )
            if more
            else None,
        )
