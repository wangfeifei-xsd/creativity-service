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
from creativity_service.modules.iam.reading import (
    read_actions,
    read_policy,
    require_action,
    resource_state,
)
from creativity_service.modules.runs.schemas import LABELS, TERMINAL
from creativity_service.modules.runs.tables import metadata as run_metadata


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
        policy = await read_policy(self.authorization, context)
        permissions = await read_actions(
            self.authorization,
            context,
            "conversation",
            "scope",
            ["conversation:read"],
            policy=policy,
        )
        require_action(permissions, "conversation:read")
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
        visible = []
        for row in candidates[:limit]:
            item_context = self.row_context(context, row)
            permissions = await read_actions(
                self.authorization,
                item_context,
                "conversation",
                row["id"],
                ["conversation:read", "conversation:write", "content:delete", "data:export"],
                policy=policy,
                state=resource_state(item_context, "conversation", row),
            )
            if "conversation:read" not in permissions:
                continue
            actions = await self.actions(item_context, row["id"], permissions, policy=policy)
            visible.append((row, item_context, actions))
        if visible:
            keys = [
                key
                for row, item_context, _ in visible
                for key in self.hooks.keys(item_context, row["id"])
            ]
            async with transaction(self.engine, context.scope, keys) as uow:
                current_rows = {
                    r["id"]: dict(r)
                    for r in (
                        await uow.connection.execute(
                            select(table).where(
                                table.c.channel_id == context.scope.channel_id,
                                table.c.id.in_([row["id"] for row, _, _ in visible]),
                            )
                        )
                    ).mappings()
                }
                blocked = await DeletionGuard(context.scope).blocked_refs(
                    uow,
                    [ContentRef("conversation", row["id"]) for row, _, _ in visible],
                    scopes=[item_context.scope for _, item_context, _ in visible],
                )
                for row, item_context, actions in visible:
                    current = current_rows.get(row["id"])
                    if (
                        current is None
                        or current["status"] in {"DELETING", "DELETED"}
                        or current["expires_at"] <= utcnow()
                    ):
                        continue
                    self.row_context(item_context, current)
                    if ContentRef("conversation", row["id"]) not in blocked:
                        items.append(self.view(current, actions))
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
        create_permissions = await read_actions(
            self.authorization,
            context,
            "conversation",
            "new",
            ["conversation:write"],
            policy=policy,
        )
        actions = (
            [VisibleAction(action_key="create", label="新建会话")]
            if executable and agents and "conversation:write" in create_permissions
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
        context, _, policy, permissions = await self.read_access(context, conversation_id)
        actions = await self.actions(context, conversation_id, permissions, policy=policy)
        async with transaction(
            self.engine, context.scope, self.hooks.keys(context, conversation_id)
        ) as uow:
            row = await self.hooks.current(uow, context, conversation_id)
            summaries = []
            summary_rows = await repo.rows(
                uow.connection,
                "conversation_summaries",
                context.scope,
                conversation_id=conversation_id,
                status="VALID",
            )
            source_rows = await Repository(metadata.tables["messages"], context.scope).get_many(
                uow.connection,
                [
                    identifier
                    for summary in summary_rows
                    for identifier in summary["source_message_ids"]
                ],
            )
            sequences = {
                identifier: message["sequence"] for identifier, message in source_rows.items()
            }
            blocked = await DeletionGuard(context.scope).blocked_refs(
                uow, [ContentRef("summary", s["id"]) for s in summary_rows]
            )
            for summary in sorted(summary_rows, key=lambda s: s["version"]):
                if ContentRef("summary", summary["id"]) in blocked:
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
        context, conversation, policy, conversation_permissions = await self.read_access(
            context, conversation_id
        )
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
        ceiling = conversation["next_sequence"] - 1 if ceiling is None else ceiling
        table = metadata.tables["messages"]
        async with self.engine.connect() as connection:
            found = [
                dict(row)
                for row in (
                    await connection.execute(
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
            turn_rows = await Repository(
                metadata.tables["conversation_turns"], context.scope
            ).find_many(
                connection, "id", [m["turn_id"] for m in selected], conversation_id=conversation_id
            )
            run_rows = await Repository(run_metadata.tables["runs"], context.scope).get_many(
                connection, [turn["run_id"] for turn in turn_rows]
            )
            contents = await Repository(
                run_metadata.tables["run_contents"], context.scope
            ).get_many(
                connection,
                [
                    run["result_ref"]
                    for run in run_rows.values()
                    if run["state"] == "SUCCEEDED" and run["result_ref"]
                ],
            )
            artifacts = await Repository(core_metadata.tables["artifacts"], context.scope).get_many(
                connection,
                [
                    part["artifact_id"]
                    for message in selected
                    for part in message["content_parts"]
                    if part["type"] == "attachment"
                ],
            )
        permissions = {}
        for turn in turn_rows:
            run = run_rows.get(turn["run_id"])
            if run is None:
                raise ServiceError("NOT_FOUND", "运行记录不存在", 404)
            allowed = (
                await read_actions(
                    self.runs.authorization,
                    context,
                    "run",
                    run["id"],
                    ["run:create", "run:read", "run:content"],
                    policy=policy,
                    state=resource_state(context, "run", run),
                )
                if not context.client_id or context.client_id == run["client_id"]
                else frozenset()
            )
            permissions[run["id"]] = (
                "conversation:write" in conversation_permissions and "run:create" in allowed,
                {"run:read", "run:content"} <= allowed,
            )
        async with transaction(
            self.engine, context.scope, self.hooks.keys(context, conversation_id)
        ) as uow:
            await self.hooks.current(uow, context, conversation_id)
            await self.runs.guard_many(uow, list(run_rows.values()))
            valid_artifacts = {
                identifier: artifact
                for identifier, artifact in artifacts.items()
                if artifact["state"] == "AVAILABLE" and artifact["expires_at"] > utcnow()
            }
            await DeletionGuard(context.scope).check(
                uow,
                [
                    *(ContentRef("message", message["id"]) for message in selected),
                    *(ContentRef("artifact", identifier) for identifier in valid_artifacts),
                ],
            )
            turns, run_states = [], {}
            for turn in sorted(turn_rows, key=lambda row: row["sequence"]):
                run = run_rows[turn["run_id"]]
                run_states[run["id"]] = run["state"]
                result = None
                content = contents.get(run["result_ref"])
                if content and content["payload"] is not None:
                    result = BusinessResult.model_validate(content["payload"])
                can_cancel, can_view = permissions[run["id"]]
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
                attachments = []
                for part in message["content_parts"]:
                    if part["type"] != "attachment":
                        continue
                    artifact = valid_artifacts.get(part["artifact_id"])
                    if artifact:
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
