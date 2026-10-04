"""独立定时扫描会话；持久化批次、幂等受理与有限重试不依赖对话埋点。"""

import logging
from datetime import timedelta
from typing import Any

from sqlalchemy import cast, func, select
from sqlalchemy.dialects.postgresql import JSONB

from creativity_service.core.context import AuthContext, TaskEnvelope
from creativity_service.core.database import Repository, UnitOfWork, transaction
from creativity_service.core.deletion import ContentRef, DeletionGuard
from creativity_service.core.primitives import ServiceError, digest, utcnow
from creativity_service.modules.agents.schemas import FrozenExecutionSpec
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
        loaded = await Repository(conversations.tables["messages"], context.scope).get_many(
            uow.connection, job["source_message_ids"]
        )
        messages = []
        for identifier in job["source_message_ids"]:
            message = loaded.get(identifier)
            if (
                not message
                or message["conversation_id"] != conversation["id"]
                or message["status"] != "COMPLETED"
            ):
                raise ServiceError("MEMORY_SOURCE_INVALID", "归档消息尚未完成或已变化", 409)
            messages.append(message)
        if not all(
            await self.memory.automatic_allowed_many(
                uow,
                context,
                source_run,
                "archive_" + job["id"][:48],
                [m["created_at"] for m in messages],
            )
        ):
            raise ServiceError("MEMORY_FORGOTTEN", "来源已被遗忘，不能重新整理", 409)
        source_runs = await Repository(run_tables.tables["runs"], context.scope).get_many(
            uow.connection, [m["run_id"] for m in messages]
        )
        if len(source_runs) != len({m["run_id"] for m in messages}):
            raise ServiceError("MEMORY_SOURCE_INVALID", "归档来源运行已删除", 409)
        await DeletionGuard(context.scope).check(
            uow,
            [
                ContentRef("conversation", conversation["id"]),
                *[ContentRef("message", m["id"]) for m in messages],
                *[ContentRef("run", m["run_id"]) for m in messages],
                *[ContentRef("snapshot", r["release_snapshot_id"]) for r in source_runs.values()],
            ],
        )
        return policy, messages

    async def discover(self, conversation: dict[str, Any]) -> None:
        if conversation["active_run_id"]:
            return
        from creativity_service.core.context import Scope

        scope = Scope.model_validate({key: conversation[key] for key in Scope.model_fields})
        runs = run_tables.tables["runs"]
        table = conversations.tables["messages"]
        contents = run_tables.tables["run_contents"]
        jobs = metadata.tables["memory_consolidations"]
        async with self.engine.connect() as connection:
            source = (
                (
                    await connection.execute(
                        select(runs)
                        .where(
                            Repository(runs, scope).predicate(),
                            runs.c.conversation_id == conversation["id"],
                            runs.c.state == "SUCCEEDED",
                            runs.c.purpose == "production",
                        )
                        .order_by(runs.c.created_at.desc(), runs.c.id)
                        .limit(1)
                    )
                )
                .mappings()
                .first()
            )
        if source is None or not source["execution_policy"].get("frozen_spec_id"):
            return
        source_run = dict(source)
        context = self.runs.context(source_run)
        self.memory.require_subject(context)
        spec = await load_spec(self.runs, source_run)
        frozen = spec.definition.context.memory_policy
        route = spec.definition.bindings.model_route_version
        if not frozen or not frozen.suggest_enabled or frozen.write_mode == "DISABLED" or not route:
            return
        await self.authorize(context, conversation["id"], source_run["id"])
        async with transaction(self.engine, context.scope, repo.keys(context.scope)) as uow:
            if await uow.connection.scalar(
                select(jobs.c.id)
                .where(
                    Repository(jobs, context.scope).predicate(),
                    jobs.c.conversation_id == conversation["id"],
                    jobs.c.state.in_(["PENDING", "ADMITTED"]),
                )
                .limit(1)
            ):
                return
            settings = await self.memory.consolidation_settings(uow, context)
            current = await Repository(conversations.tables["conversations"], context.scope).get(
                uow.connection, conversation["id"]
            )
            if (
                not settings.enabled
                or not current
                or current["active_run_id"]
                or current["updated_at"] > utcnow() - timedelta(seconds=settings.idle_seconds)
            ):
                return
            preference = await repo.one(uow.connection, "memory_preferences", context.scope)
            if preference and not preference["enabled"]:
                return
            deletions = metadata.tables["memory_deletion_jobs"]
            cutoff = await uow.connection.scalar(
                select(func.max(deletions.c.created_at)).where(
                    Repository(deletions, context.scope).predicate(),
                    deletions.c.kind == "CLEAR",
                )
            )
            if preference:
                cutoff = (
                    max(cutoff, preference["updated_at"]) if cutoff else preference["updated_at"]
                )
            used = (
                select(jobs.c.id)
                .where(
                    Repository(jobs, context.scope).predicate(),
                    jobs.c.conversation_id == conversation["id"],
                    jobs.c.source_message_ids.op("?")(table.c.id),
                )
                .exists()
            )
            frozen_policy = cast(contents.c.payload["payload_json"].astext, JSONB)["definition"][
                "context"
            ]["memory_policy"]
            peer = table.alias("turn_peer")
            complete_turn = (
                select(peer.c.id)
                .where(
                    *(peer.c[key] == value for key, value in context.scope.model_dump().items()),
                    peer.c.conversation_id == conversation["id"],
                    peer.c.turn_id == table.c.turn_id,
                    peer.c.run_id == table.c.run_id,
                    peer.c.status == "COMPLETED",
                    peer.c.role.in_(["user", "assistant"]),
                    peer.c.role != table.c.role,
                )
                .exists()
            )
            statement = (
                select(table)
                .select_from(
                    table.join(runs, runs.c.id == table.c.run_id).join(
                        contents,
                        contents.c.run_id == runs.c.id,
                    )
                )
                .where(
                    Repository(table, context.scope).predicate(),
                    Repository(runs, context.scope).predicate(),
                    Repository(contents, context.scope).predicate(),
                    contents.c.kind == "execution_spec",
                    table.c.conversation_id == conversation["id"],
                    table.c.status == "COMPLETED",
                    table.c.role.in_(["user", "assistant"]),
                    runs.c.state == "SUCCEEDED",
                    runs.c.purpose == "production",
                    complete_turn,
                    ~used,
                    frozen_policy["suggest_enabled"].as_boolean().is_(True),
                    frozen_policy["write_mode"].astext != "DISABLED",
                )
            )
            if cutoff:
                statement = statement.where(table.c.created_at > cutoff, runs.c.created_at > cutoff)
            # 每轮固定一条用户消息和一条助手消息，多取一条以保证奇数批量不拆轮次。
            available = [
                dict(row)
                for row in (
                    await uow.connection.execute(
                        statement.order_by(table.c.sequence, table.c.id).limit(
                            settings.batch_messages + 1
                        )
                    )
                ).mappings()
            ]
            if len(available) < 2:
                return
            selected = available[: settings.batch_messages]
            if (
                len(available) > len(selected)
                and available[-1]["turn_id"] == selected[-1]["turn_id"]
            ):
                selected.append(available[-1])
            # 完整轮次与其冻结策略一起校验，已消费历史不再参与下一批策略交集。
            run_ids = list({m["run_id"] for m in selected})
            batch_runs = await Repository(runs, context.scope).get_many(uow.connection, run_ids)
            specs = {
                r["run_id"]: r
                for r in await Repository(contents, context.scope).find_many(
                    uow.connection, "run_id", run_ids, kind="execution_spec"
                )
            }
            for run in batch_runs.values():
                saved = specs.get(run["id"])
                if saved is None:
                    raise ServiceError("MEMORY_SOURCE_INVALID", "归档来源定义已删除", 409)
                previous = FrozenExecutionSpec.model_validate(
                    saved["payload"]
                ).definition.context.memory_policy
                if previous is None:
                    return
                frozen = self.memory.intersect_policy(frozen, previous)
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
