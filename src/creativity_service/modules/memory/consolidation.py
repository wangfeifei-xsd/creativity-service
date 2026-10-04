"""独立定时扫描会话；持久化批次、幂等受理与有限重试不依赖对话埋点。"""

import logging
from datetime import timedelta
from typing import Any

from sqlalchemy import select

from creativity_service.core.context import AuthContext, TaskEnvelope
from creativity_service.core.database import Repository, UnitOfWork, transaction
from creativity_service.core.deletion import ContentRef, DeletionGuard
from creativity_service.core.primitives import ServiceError, digest, utcnow
from creativity_service.modules.conversations.tables import metadata as conversations
from creativity_service.modules.memory import repositories as repo
from creativity_service.modules.memory.schemas import MemoryPolicy
from creativity_service.modules.memory.services import MemoryService
from creativity_service.modules.memory.tables import metadata
from creativity_service.modules.runs.schemas import TERMINAL
from creativity_service.modules.runs.services import RunService
from creativity_service.modules.runs.tables import metadata as run_tables
from creativity_service.modules.runtime.admission import RuntimeAdmission
from creativity_service.modules.runtime.storage import load_spec

logger = logging.getLogger(__name__)


class MemoryConsolidation:
    def __init__(
        self, memory: MemoryService, runs: RunService, admission: RuntimeAdmission
    ) -> None:
        self.memory, self.runs, self.admission = memory, runs, admission
        self.engine = memory.engine

    async def authorize(
        self, context: AuthContext, conversation_id: str, source_run_id: str
    ) -> None:
        for action, resource in (
            ("memory:write", "scope"),
            ("memory:read", "scope"),
            ("conversation:read", conversation_id),
        ):
            await self.memory.authorization.require(context, action, resource)
        source = await self.runs.load(
            TaskEnvelope(channel_id=context.scope.channel_id, run_id=source_run_id)
        )
        await self.runs.authorization.require(context, "run:create", source_run_id)
        if self.runs.boundary:
            await self.runs.boundary(context, source)

    async def current(
        self, uow: UnitOfWork, context: AuthContext, job: dict[str, Any]
    ) -> tuple[MemoryPolicy, list[dict[str, Any]]]:
        source_run = await Repository(run_tables.tables["runs"], context.scope).get(
            uow.connection, job["source_run_id"]
        )
        if not source_run:
            raise ServiceError("NOT_FOUND", "归档来源运行不可用", 404)
        frozen = MemoryPolicy.model_validate(job["settings"]["policy"])
        policy = self.memory.intersect_policy(
            await self.memory.effective_policy(uow, context, source_run["agent_id"], frozen), frozen
        )
        preference = await self.memory.preference(uow, context, [])
        settings = await self.memory.consolidation_settings(uow, context)
        if (
            not settings.enabled
            or not policy.suggest_enabled
            or policy.write_mode == "DISABLED"
            or not preference.enabled
            or preference.revision != job["preference_revision"]
        ):
            raise ServiceError("MEMORY_DISABLED", "后台记忆整理已关闭或主体设置发生变化", 409)
        if [a.model_dump() for a in await self.memory.attributes(uow, context)] != job["settings"][
            "attributes"
        ]:
            raise ServiceError("MEMORY_POLICY_CHANGED", "画像属性已变化，请重新整理", 409)
        conversation = await Repository(conversations.tables["conversations"], context.scope).get(
            uow.connection, job["conversation_id"]
        )
        if (
            not conversation
            or conversation["status"] not in {"ACTIVE", "ARCHIVED"}
            or conversation["expires_at"] <= utcnow()
        ):
            raise ServiceError("NOT_FOUND", "归档来源会话已失效", 404)
        messages = []
        for identifier in job["source_message_ids"]:
            message = await Repository(conversations.tables["messages"], context.scope).get(
                uow.connection, identifier
            )
            if (
                not message
                or message["conversation_id"] != conversation["id"]
                or message["status"] != "COMPLETED"
            ):
                raise ServiceError("MEMORY_SOURCE_INVALID", "归档消息尚未完成或已变化", 409)
            if not await self.memory.automatic_allowed(
                uow, context, source_run, "archive_" + job["id"][:48], message["created_at"]
            ):
                raise ServiceError("MEMORY_FORGOTTEN", "来源已被遗忘，不能重新整理", 409)
            messages.append(message)
        await DeletionGuard(context.scope).check(
            uow,
            [
                ContentRef("conversation", conversation["id"]),
                *[ContentRef("message", m["id"]) for m in messages],
                *[ContentRef("run", m["run_id"]) for m in messages],
            ],
        )
        return policy, messages

    async def discover(self, conversation: dict[str, Any]) -> None:
        table = conversations.tables["messages"]
        async with self.engine.connect() as connection:
            messages = [
                dict(v)
                for v in (
                    await connection.execute(
                        select(table)
                        .where(
                            table.c.channel_id == conversation["channel_id"],
                            table.c.conversation_id == conversation["id"],
                            table.c.status == "COMPLETED",
                            table.c.role.in_(["user", "assistant"]),
                        )
                        .order_by(table.c.sequence)
                    )
                ).mappings()
            ]
        if len(messages) < 2:
            return
        source_run = await self.runs.load(
            TaskEnvelope(channel_id=conversation["channel_id"], run_id=messages[-1]["run_id"])
        )
        context = self.runs.context(source_run)
        self.memory.require_subject(context)
        if (
            source_run["purpose"] != "production"
            or source_run["state"] != "SUCCEEDED"
            or not source_run["execution_policy"].get("frozen_spec_id")
        ):
            return
        spec = await load_spec(self.runs, source_run)
        frozen = spec.definition.context.memory_policy
        route = spec.definition.bindings.model_route_version
        if not frozen or not frozen.suggest_enabled or frozen.write_mode == "DISABLED" or not route:
            return
        await self.authorize(context, conversation["id"], source_run["id"])
        eligible = set()
        for run_id in dict.fromkeys(m["run_id"] for m in messages):
            run = await self.runs.load(
                TaskEnvelope(channel_id=context.scope.channel_id, run_id=run_id)
            )
            if (
                run["purpose"] != "production"
                or run["state"] != "SUCCEEDED"
                or not run["execution_policy"].get("frozen_spec_id")
            ):
                continue
            try:
                previous = (await load_spec(self.runs, run)).definition.context.memory_policy
            except ServiceError as exc:
                if exc.status not in {404, 410}:
                    raise
                # 已删除轮次不再参与整理，也不能挡住之后产生的新消息。
                continue
            if previous and previous.suggest_enabled and previous.write_mode != "DISABLED":
                eligible.add(run_id)
                frozen = self.memory.intersect_policy(frozen, previous)
        messages = [m for m in messages if m["run_id"] in eligible]
        async with transaction(self.engine, context.scope, repo.keys(context.scope)) as uow:
            jobs = await repo.rows(
                uow.connection,
                "memory_consolidations",
                context.scope,
                conversation_id=conversation["id"],
            )
            if any(j["state"] in {"PENDING", "ADMITTED"} for j in jobs):
                return
            completed = {i for j in jobs for i in j["source_message_ids"]}
            settings = await self.memory.consolidation_settings(uow, context)
            current = await Repository(conversations.tables["conversations"], context.scope).get(
                uow.connection, conversation["id"]
            )
            if (
                not current
                or current["active_run_id"]
                or current["updated_at"] > utcnow() - timedelta(seconds=settings.idle_seconds)
            ):
                return
            available = [
                m
                for m in messages
                if m["id"] not in completed
                and await self.memory.automatic_allowed(
                    uow, context, source_run, "", m["created_at"]
                )
            ]
            if len(available) < 2:
                return
            selected = available[: settings.batch_messages]
            # 批次只在完整轮次之间切分，不能把用户条件和对应结果拆开。
            last_turn = selected[-1]["turn_id"]
            selected += [
                m for m in available[settings.batch_messages :] if m["turn_id"] == last_turn
            ]
            identifiers = [m["id"] for m in selected]
            identifier = digest([context.scope.model_dump(), conversation["id"], identifiers])
            policy = self.memory.intersect_policy(
                await self.memory.effective_policy(uow, context, source_run["agent_id"], frozen),
                frozen,
            )
            job = {
                "id": identifier,
                "conversation_id": conversation["id"],
                "source_run_id": source_run["id"],
                "source_message_ids": identifiers,
                "memory_ids": [],
                "generation_run_id": None,
                "state": "PENDING",
                "attempt": 1,
                "next_attempt_at": utcnow(),
                "error_code": None,
                "settings": {
                    "policy": policy.model_dump(),
                    "attributes": [
                        a.model_dump() for a in await self.memory.attributes(uow, context)
                    ],
                    "route": route,
                },
                "preference_revision": (await self.memory.preference(uow, context, [])).revision,
            }
            await self.current(uow, context, job)
            await repo.save(
                uow,
                "memory_consolidations",
                identifier,
                {k: v for k, v in job.items() if k != "id"},
            )

    async def advance(self, row: dict[str, Any]) -> None:
        source = await self.runs.load(
            TaskEnvelope(channel_id=row["channel_id"], run_id=row["source_run_id"])
        )
        context = self.runs.context(source)
        await self.authorize(context, row["conversation_id"], row["source_run_id"])
        async with transaction(self.engine, context.scope, repo.keys(context.scope)) as uow:
            job = await repo.required(
                uow.connection, "memory_consolidations", context.scope, id=row["id"]
            )
            if job["state"] not in {"PENDING", "ADMITTED"} or job["next_attempt_at"] > utcnow():
                return
            await self.current(uow, context, job)
        if job["generation_run_id"]:
            run = await self.runs.load(
                TaskEnvelope(channel_id=context.scope.channel_id, run_id=job["generation_run_id"])
            )
            if run["state"] not in TERMINAL:
                return
            async with transaction(self.engine, context.scope, repo.keys(context.scope)) as uow:
                current = await repo.required(
                    uow.connection, "memory_consolidations", context.scope, id=job["id"]
                )
                if current["revision"] != job["revision"] or current["state"] == "COMPLETED":
                    return
                if run["state"] == "CANCELLED":
                    # 主动取消不消耗重试轮次，也不能由下次扫描自动重启。
                    await repo.save(
                        uow,
                        "memory_consolidations",
                        job["id"],
                        {"state": "SKIPPED", "error_code": "RUN_CANCELLED"},
                    )
                    return
                await repo.save(
                    uow,
                    "memory_consolidations",
                    job["id"],
                    {
                        "state": "FAILED" if job["attempt"] >= 3 else "PENDING",
                        "attempt": job["attempt"] if job["attempt"] >= 3 else job["attempt"] + 1,
                        "generation_run_id": None,
                        "next_attempt_at": utcnow() + timedelta(seconds=60 * 2 ** job["attempt"]),
                        "error_code": (run.get("error") or {}).get(
                            "code", "MEMORY_GENERATION_FAILED"
                        ),
                    },
                )
            return
        from creativity_service.modules.memory.generation import freeze_generation

        spec = await freeze_generation(self, context, job)
        receipt = await self.admission.submit(
            context, spec, {"job_id": job["id"]}, digest(["memory", job["id"], job["attempt"]])
        )
        async with transaction(self.engine, context.scope, repo.keys(context.scope)) as uow:
            current = await repo.required(
                uow.connection, "memory_consolidations", context.scope, id=job["id"]
            )
            if current["state"] == "PENDING" and current["attempt"] == job["attempt"]:
                await repo.save(
                    uow,
                    "memory_consolidations",
                    job["id"],
                    {
                        "generation_run_id": receipt.run_id,
                        "state": "ADMITTED",
                        "error_code": None,
                    },
                )

    async def sweep(self, channel_id: str) -> None:
        # 分页固定渠道，不允许系统渠道作为全局业务查询范围。
        table = conversations.tables["conversations"]
        after = ""
        while True:
            async with self.engine.connect() as connection:
                rows = [
                    dict(v)
                    for v in (
                        await connection.execute(
                            select(table)
                            .where(
                                table.c.channel_id == channel_id,
                                table.c.subject_id.is_not(None),
                                table.c.status.in_(["ACTIVE", "ARCHIVED"]),
                                table.c.expires_at > utcnow(),
                                table.c.id > after,
                            )
                            .order_by(table.c.id)
                            .limit(100)
                        )
                    ).mappings()
                ]
            for row in rows:
                try:
                    await self.discover(row)
                except ServiceError as exc:
                    logger.info(
                        "会话暂不满足后台整理条件",
                        extra={
                            "channel_id": channel_id,
                            "conversation_id": row["id"],
                            "error_code": exc.code,
                        },
                    )
            if len(rows) < 100:
                break
            after = rows[-1]["id"]
        table = metadata.tables["memory_consolidations"]
        async with self.engine.connect() as connection:
            jobs = [
                dict(v)
                for v in (
                    await connection.execute(
                        select(table)
                        .where(
                            table.c.channel_id == channel_id,
                            table.c.state.in_(["PENDING", "ADMITTED"]),
                            table.c.next_attempt_at <= utcnow(),
                        )
                        .order_by(table.c.next_attempt_at)
                        .limit(100)
                    )
                ).mappings()
            ]
        for job in jobs:
            try:
                await self.advance(job)
            except ServiceError as exc:
                context = self.runs.context(
                    await self.runs.load(
                        TaskEnvelope(channel_id=channel_id, run_id=job["source_run_id"])
                    )
                )
                async with transaction(self.engine, context.scope, repo.keys(context.scope)) as uow:
                    current = await repo.required(
                        uow.connection, "memory_consolidations", context.scope, id=job["id"]
                    )
                    if (
                        current["state"] not in {"PENDING", "ADMITTED"}
                        or current["revision"] != job["revision"]
                    ):
                        continue
                    # 准入额度可能随占用释放而恢复，沿用原批次与幂等键等待重试。
                    retryable = exc.status >= 500 or (
                        exc.status == 429
                        and exc.code in {"BUDGET_EXCEEDED", "PLATFORM_LIMIT_EXCEEDED"}
                    )
                    await repo.save(
                        uow,
                        "memory_consolidations",
                        job["id"],
                        {
                            "state": current["state"] if retryable else "SKIPPED",
                            "error_code": exc.code,
                            "next_attempt_at": utcnow() + timedelta(minutes=5),
                        },
                    )
