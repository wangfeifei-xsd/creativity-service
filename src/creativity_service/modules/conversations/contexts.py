"""优先保留必要指令与当前输入，冻结实际历史来源和删减记录。"""

from typing import Any

from creativity_service.core.context import AuthContext
from creativity_service.core.contracts import BusinessResult
from creativity_service.core.database import transaction
from creativity_service.core.deletion import ContentRef, DeletionGuard
from creativity_service.core.primitives import (
    ServiceError,
    canonical_json,
    digest,
    new_id,
    unavailable,
)
from creativity_service.modules.conversations import repositories as repo
from creativity_service.modules.conversations.base import ConversationKernel
from creativity_service.modules.conversations.schemas import SelectedContext, SummaryView
from creativity_service.modules.runs import repositories as run_repo


class ContextService(ConversationKernel):
    async def select_context(
        self,
        context: AuthContext,
        conversation_id: str,
        run_id: str,
        instructions: str,
        *,
        max_characters: int = 16000,
        recent_messages: int = 20,
    ) -> SelectedContext:
        context = await self.access(context, conversation_id)
        if not 1 <= max_characters <= 1000000 or not 0 <= recent_messages <= 200:
            raise ServiceError("CONTEXT_POLICY_INVALID", "上下文选择配置无效", 422)
        selection_digest = digest([instructions, max_characters, recent_messages])
        snapshot_id = digest([conversation_id, run_id, "context-v1"])
        async with transaction(
            self.engine, context.scope, self.hooks.keys(context, conversation_id)
        ) as uow:
            await self.hooks.current(uow, context, conversation_id)
            turn = await repo.required(
                uow.connection,
                "conversation_turns",
                context.scope,
                conversation_id=conversation_id,
                run_id=run_id,
            )
            run = await run_repo.required(
                uow.connection, "runs", context.scope.channel_id, id=run_id
            )
            await self.runs.guard(uow, run)
            current_message = await repo.required(
                uow.connection, "messages", context.scope, id=turn["user_message_id"]
            )
            await DeletionGuard(context.scope).check(
                uow, [ContentRef("message", current_message["id"])]
            )
            followup_result = None
            if turn["source_run_id"]:
                previous_run = await run_repo.required(
                    uow.connection, "runs", context.scope.channel_id, id=turn["source_run_id"]
                )
                await self.runs.guard(uow, previous_run)
                content = await run_repo.required(
                    uow.connection,
                    "run_contents",
                    context.scope.channel_id,
                    id=previous_run["result_ref"],
                )
                followup_result = BusinessResult.model_validate(content["payload"])
            required = len(instructions) + len(
                canonical_json(
                    [
                        turn["input"],
                        current_message["content_parts"],
                        turn["confirmed_conditions"],
                        followup_result.model_dump(mode="json") if followup_result else None,
                    ]
                ).decode()
            )
            if required > max_characters:
                raise ServiceError(
                    "CONTEXT_REQUIRED_TOO_LARGE",
                    "当前任务和必要指令已超出上下文容量，请缩短输入",
                    422,
                )
            all_messages = await repo.rows(
                uow.connection, "messages", context.scope, conversation_id=conversation_id
            )
            by_id = {m["id"]: m for m in all_messages}
            existing = await repo.one(
                uow.connection, "context_snapshots", context.scope, run_id=run_id
            )
            summary = None
            if existing:
                if existing["truncation"]["selection_digest"] != selection_digest:
                    raise ServiceError(
                        "CONTEXT_SNAPSHOT_CONFLICT", "本轮上下文已经冻结，不能更换选择配置", 409
                    )
                await DeletionGuard(context.scope).check(
                    uow, [ContentRef("context", existing["id"])]
                )
                try:
                    included = [
                        by_id[i]
                        for i in existing["included_message_ids"]
                        if i != current_message["id"]
                    ]
                except KeyError as exc:
                    raise ServiceError("CONTENT_DELETED", "本轮历史来源已经清理", 410) from exc
                if existing["summary_id"]:
                    summary = await repo.required(
                        uow.connection,
                        "conversation_summaries",
                        context.scope,
                        id=existing["summary_id"],
                    )
                    if summary["status"] != "VALID":
                        raise ServiceError("CONTENT_DELETED", "本轮摘要已失效", 410)
                truncation = existing["truncation"]
            else:
                previous_turns = await repo.rows(
                    uow.connection,
                    "conversation_turns",
                    context.scope,
                    conversation_id=conversation_id,
                )
                completed_runs = set()
                for old_turn in previous_turns:
                    if old_turn["sequence"] < turn["sequence"]:
                        old_run = await run_repo.required(
                            uow.connection, "runs", context.scope.channel_id, id=old_turn["run_id"]
                        )
                        if old_run["state"] == "SUCCEEDED":
                            completed_runs.add(old_run["id"])
                eligible = sorted(
                    (m for m in all_messages if m["run_id"] in completed_runs),
                    key=lambda m: m["sequence"],
                    reverse=True,
                )
                available, included = max_characters - required, []
                for message in eligible[:recent_messages]:
                    await DeletionGuard(context.scope).check(
                        uow, [ContentRef("message", message["id"])]
                    )
                    size = len(canonical_json(message["content_parts"]).decode())
                    if size > available:
                        break
                    included.append(message)
                    available -= size
                included.reverse()
                included_ids = {m["id"] for m in included}
                summaries = sorted(
                    await repo.rows(
                        uow.connection,
                        "conversation_summaries",
                        context.scope,
                        conversation_id=conversation_id,
                        status="VALID",
                    ),
                    key=lambda s: s["version"],
                    reverse=True,
                )
                eligible_ids = {m["id"] for m in eligible}
                for candidate in summaries:
                    if (
                        not set(candidate["source_message_ids"]) <= eligible_ids
                        or set(candidate["source_message_ids"]) & included_ids
                        or len(candidate["content"]) > available
                    ):
                        continue
                    try:
                        await DeletionGuard(context.scope).check(
                            uow, [ContentRef("summary", candidate["id"])]
                        )
                    except ServiceError as exc:
                        if exc.code != "CONTENT_DELETED":
                            raise
                        continue
                    summary = candidate
                    break
                truncation = {
                    "policy": "必要指令与当前任务优先，其次最近完整消息和有效摘要",
                    "max_characters": max_characters,
                    "recent_messages": recent_messages,
                    "selection_digest": selection_digest,
                    "omitted_message_ids": [
                        m["id"] for m in eligible if m["id"] not in included_ids
                    ],
                    "omitted_sequences": sorted(
                        m["sequence"] for m in eligible if m["id"] not in included_ids
                    ),
                    "reason": "超过消息窗口或字符容量"
                    if len(included) < len(eligible)
                    else "无截断",
                }
                await repo.save(
                    uow,
                    "context_snapshots",
                    snapshot_id,
                    {
                        "conversation_id": conversation_id,
                        "run_id": run_id,
                        "included_message_ids": [m["id"] for m in included]
                        + [current_message["id"]],
                        "summary_version": str(summary["version"]) if summary else None,
                        "summary_id": summary["id"] if summary else None,
                        "summary_source_ids": summary["source_message_ids"] if summary else [],
                        "memory_refs": [],
                        "truncation": truncation,
                        "policy_version": "characters-v1",
                        "required_characters": required,
                    },
                )
                await self.hooks.link(
                    uow,
                    context,
                    ContentRef("conversation", conversation_id),
                    ContentRef("context", snapshot_id),
                )
                for message in [*included, current_message]:
                    await self.hooks.link(
                        uow,
                        context,
                        ContentRef("message", message["id"]),
                        ContentRef("context", snapshot_id),
                    )
                if summary:
                    await self.hooks.link(
                        uow,
                        context,
                        ContentRef("summary", summary["id"]),
                        ContentRef("context", snapshot_id),
                    )
            for message in included:
                await DeletionGuard(context.scope).check(
                    uow, [ContentRef("message", message["id"])]
                )
            if summary:
                await DeletionGuard(context.scope).check(
                    uow, [ContentRef("summary", summary["id"])]
                )
            return SelectedContext(
                run_id=run_id,
                instructions=instructions,
                current_input=turn["input"],
                current_content_parts=current_message["content_parts"],
                source_run_id=turn["source_run_id"],
                confirmed_conditions=turn["confirmed_conditions"],
                followup_result=followup_result,
                messages=[
                    {"role": m["role"], "content_parts": m["content_parts"]} for m in included
                ],
                summary=summary["content"] if summary else None,
                included_message_ids=[m["id"] for m in included] + [current_message["id"]],
                truncation=truncation,
            )

    async def record_summary(
        self,
        context: AuthContext,
        conversation_id: str,
        source_message_ids: list[str],
        content: str,
        *,
        truncation: dict[str, Any] | None = None,
        generation_run_id: str | None = None,
    ) -> SummaryView:
        """内部来源登记入口；模型摘要必须经 generate_summary 的受控计量端口。"""
        context = await self.access(context, conversation_id, "conversation:write")
        if (
            not source_message_ids
            or len(source_message_ids) != len(set(source_message_ids))
            or not content.strip()
            or len(content) > 100000
        ):
            raise ServiceError("SUMMARY_INVALID", "摘要内容和来源不正确", 422)
        async with transaction(
            self.engine, context.scope, self.hooks.keys(context, conversation_id)
        ) as uow:
            await self.hooks.current(uow, context, conversation_id)
            sequences = []
            for message_id in source_message_ids:
                message = await repo.required(
                    uow.connection,
                    "messages",
                    context.scope,
                    id=message_id,
                    conversation_id=conversation_id,
                )
                run = await run_repo.required(
                    uow.connection, "runs", context.scope.channel_id, id=message["run_id"]
                )
                if run["state"] != "SUCCEEDED":
                    raise ServiceError("SUMMARY_SOURCE_INCOMPLETE", "摘要只能使用已完成轮次", 409)
                await DeletionGuard(context.scope).check(uow, [ContentRef("message", message_id)])
                sequences.append(message["sequence"])
            if generation_run_id:
                generated = await run_repo.required(
                    uow.connection, "runs", context.scope.channel_id, id=generation_run_id
                )
                run_repo.verify_scope(generated, context.scope)
                if generated["state"] != "SUCCEEDED":
                    raise ServiceError("SUMMARY_GENERATION_INCOMPLETE", "摘要生成尚未完成", 409)
                await self.runs.guard(uow, generated)
            summaries = await repo.rows(
                uow.connection,
                "conversation_summaries",
                context.scope,
                conversation_id=conversation_id,
            )
            summary_id = new_id("summary")
            row = await repo.save(
                uow,
                "conversation_summaries",
                summary_id,
                {
                    "conversation_id": conversation_id,
                    "source_message_ids": source_message_ids,
                    "version": max((s["version"] for s in summaries), default=0) + 1,
                    "content": content,
                    "status": "VALID",
                    "truncation": truncation or {},
                    "generation_run_id": generation_run_id,
                },
            )
            await self.hooks.link(
                uow,
                context,
                ContentRef("conversation", conversation_id),
                ContentRef("summary", summary_id),
            )
            for message_id in source_message_ids:
                await self.hooks.link(
                    uow,
                    context,
                    ContentRef("message", message_id),
                    ContentRef("summary", summary_id),
                )
            return SummaryView(
                summary_id=row["id"],
                version=row["version"],
                content=row["content"],
                source_message_ids=source_message_ids,
                source_sequences=sequences,
                truncation=row["truncation"],
                created_at=row["created_at"],
            )

    async def generate_summary(
        self, context: AuthContext, conversation_id: str, source_message_ids: list[str]
    ) -> SummaryView:
        context = await self.access(context, conversation_id, "conversation:write")
        if self.generator is None:
            raise unavailable("受控摘要生成服务")
        messages = []
        async with transaction(
            self.engine, context.scope, self.hooks.keys(context, conversation_id)
        ) as uow:
            await self.hooks.current(uow, context, conversation_id)
            for message_id in source_message_ids:
                row = await repo.required(
                    uow.connection,
                    "messages",
                    context.scope,
                    id=message_id,
                    conversation_id=conversation_id,
                )
                await DeletionGuard(context.scope).check(uow, [ContentRef("message", message_id)])
                run = await run_repo.required(
                    uow.connection, "runs", context.scope.channel_id, id=row["run_id"]
                )
                if run["state"] != "SUCCEEDED":
                    raise ServiceError("SUMMARY_SOURCE_INCOMPLETE", "摘要只能使用已完成轮次", 409)
                messages.append(
                    {
                        "role": row["role"],
                        "text": "\n".join(
                            p["text"] for p in row["content_parts"] if p["type"] == "text"
                        ),
                    }
                )
        content, run_id = await self.generator.generate(context, messages)
        return await self.record_summary(
            context, conversation_id, source_message_ids, content, generation_run_id=run_id
        )
