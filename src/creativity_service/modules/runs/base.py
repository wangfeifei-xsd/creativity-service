"""状态、租约、内容与事件的事务内共同协议。"""

from collections.abc import Awaitable, Callable
from datetime import timedelta
from typing import Any

from sqlalchemy.ext.asyncio import AsyncEngine

from creativity_service.core.context import AuthContext, Authorization, Scope, TaskEnvelope
from creativity_service.core.contracts import ReleaseSnapshot, ResourceVersion, RunError
from creativity_service.core.database import Repository, UnitOfWork, transaction
from creativity_service.core.database.tables import metadata as core_metadata
from creativity_service.core.deletion import ContentRef, DeletionGuard, content_key
from creativity_service.core.locking import ResourceKey
from creativity_service.core.primitives import (
    RunInput,
    ServiceError,
    digest,
    new_id,
    unavailable,
    utcnow,
)
from creativity_service.core.versioning import VersionService
from creativity_service.modules.budgets.services import BudgetService
from creativity_service.modules.runs.ports import DefinitionResolver, Executor, TurnHooks
from creativity_service.modules.runs.repositories import (
    conversation_key,
    one,
    required,
    rows,
    run_key,
    save,
    verify_scope,
)
from creativity_service.modules.runs.schemas import (
    LABELS,
    TERMINAL,
    TRANSITIONS,
    AdmissionReceipt,
    Lease,
)
from creativity_service.modules.usage.services import UsageService


class RunKernel:
    def __init__(
        self,
        engine: AsyncEngine,
        authorization: Authorization,
        versions: VersionService,
        budgets: BudgetService,
        ledger: UsageService,
        *,
        resolver: DefinitionResolver | None = None,
        turns: TurnHooks | None = None,
        lease_seconds: int = 30,
        fault: Callable[[str], None] | None = None,
    ) -> None:
        if not 1 <= lease_seconds <= 300:
            raise ValueError("执行租约时长超出范围")
        self.engine, self.authorization, self.versions = engine, authorization, versions
        self.budgets, self.ledger, self.resolver, self.turns = budgets, ledger, resolver, turns
        self.lease_seconds, self.fault = lease_seconds, fault or (lambda _point: None)
        self.boundary: Callable[[AuthContext, dict[str, Any]], Awaitable[None]] | None = None
        self.runtime_executor: Executor | None = None
        self.rerun_handler: (
            Callable[[AuthContext, dict[str, Any], RunInput, str], Awaitable[AdmissionReceipt]]
            | None
        ) = None

    @staticmethod
    def context(row: dict[str, Any]) -> AuthContext:
        return AuthContext.model_validate(row["identity"])

    async def load(self, message: TaskEnvelope) -> dict[str, Any]:
        async with self.engine.connect() as connection:
            return await required(connection, "runs", message.channel_id, id=message.run_id)

    async def access_context(self, context: AuthContext, run_id: str) -> AuthContext:
        row = await self.load(TaskEnvelope(channel_id=context.scope.channel_id, run_id=run_id))
        scope = context.scope
        if (
            context.principal_type == "management"
            and scope.subject_id is None
            and row["environment"] == scope.environment
            and row["data_scope_id"] == scope.data_scope_id
        ):
            # 管理员的数据域来自当前工作区；具体主体只从已存运行恢复并重新授权。
            return context.model_copy(
                update={
                    "scope": Scope.model_validate({key: row[key] for key in Scope.model_fields})
                }
            )
        verify_scope(row, scope)
        return context

    async def load_and_authorize(self, message: TaskEnvelope) -> AuthContext:
        row = await self.load(message)
        context = self.context(row)
        verify_scope(row, context.scope)
        await self.authorization.require(context, "run:create", row["id"])
        return context

    def keys(
        self, context: AuthContext, run_id: str, conversation_id: str | None = None
    ) -> list[ResourceKey]:
        keys = [
            run_key(context.scope.channel_id, run_id),
            content_key(context.scope),
            *self.budgets.admission_keys(context, run_id),
        ]
        if conversation_id:
            keys.append(conversation_key(context.scope, conversation_id))
            if self.turns:
                keys.extend(self.turns.keys(context, conversation_id))
        return keys

    async def locked_run(self, uow: UnitOfWork, run_id: str) -> dict[str, Any]:
        row = await required(uow.connection, "runs", uow.scope.channel_id, id=run_id)
        if not isinstance(uow.scope, Scope):
            raise ServiceError("SCOPE_MISMATCH", "运行不能使用控制面范围", 403)
        verify_scope(row, uow.scope)
        return row

    async def guard(self, uow: UnitOfWork, row: dict[str, Any]) -> None:
        refs = [ContentRef("run", row["id"]), ContentRef("snapshot", row["release_snapshot_id"])]
        if row["conversation_id"]:
            refs.append(ContentRef("conversation", row["conversation_id"]))
        await DeletionGuard(self.context(row).scope).check(uow, refs)

    async def snapshot(self, uow: UnitOfWork, row: dict[str, Any]) -> ReleaseSnapshot:
        await self.guard(uow, row)
        scope = self.context(row).scope
        stored = await Repository(core_metadata.tables["release_snapshots"], scope).get(
            uow.connection, row["release_snapshot_id"]
        )
        if not stored or stored["run_id"] != row["id"]:
            raise ServiceError("SNAPSHOT_INVALID", "运行快照不可用", 409)
        value = ReleaseSnapshot(
            snapshot_id=stored["id"],
            scope=scope,
            run_id=row["id"],
            purpose=stored["purpose"],
            versions=tuple(ResourceVersion.model_validate(v) for v in stored["versions"]),
            dependencies_digest=stored["dependencies_digest"],
            output_schema=stored["output_schema"],
            captured_at=stored["created_at"],
        )
        if digest(stored["versions"]) != value.dependencies_digest:
            raise ServiceError("SNAPSHOT_INVALID", "运行快照校验失败", 409)
        return value

    async def content(
        self,
        uow: UnitOfWork,
        row: dict[str, Any],
        kind: str,
        payload: Any,
        *,
        sensitive: bool = True,
    ) -> str:
        if sensitive:
            await self.guard(uow, row)
        content_id = new_id("content")
        await save(
            uow, "run_contents", content_id, {"run_id": row["id"], "kind": kind, "payload": payload}
        )
        return content_id

    async def event(
        self,
        uow: UnitOfWork,
        row: dict[str, Any],
        event_type: str,
        payload: Any,
        *,
        sensitive: bool = True,
    ) -> dict[str, Any]:
        payload_ref = await self.content(uow, row, "event", payload, sensitive=sensitive)
        sequence = row["event_sequence"] + 1
        await save(
            uow,
            "run_events",
            new_id("event"),
            {
                "run_id": row["id"],
                "sequence": sequence,
                "event_type": event_type,
                "payload_ref": payload_ref,
                "expires_at": utcnow() + timedelta(hours=24),
            },
        )
        row.update(await save(uow, "runs", row["id"], {"event_sequence": sequence}))
        return row

    @staticmethod
    def error(row: dict[str, Any], code: str, message: str) -> dict[str, Any]:
        return RunError(
            code=code,
            message=message,
            stage="execution",
            retryable=False,
            request_id=row["identity"]["request_id"],
        ).model_dump(mode="json")

    async def transition(
        self,
        uow: UnitOfWork,
        row: dict[str, Any],
        state: str,
        *,
        error: dict[str, Any] | None = None,
        result_ref: str | None = None,
    ) -> dict[str, Any]:
        if state not in TRANSITIONS.get(row["state"], set()):
            raise ServiceError("RUN_STATE_CONFLICT", "运行状态不允许此变更", 409)
        row.update(
            await save(
                uow,
                "runs",
                row["id"],
                {
                    "state": state,
                    "error": error,
                    "result_ref": result_ref,
                    "completed_at": utcnow() if state in TERMINAL else None,
                },
            )
        )
        if state in TERMINAL:
            if error:
                await self.event(uow, row, "error", error, sensitive=False)
            await self.event(
                uow,
                row,
                "completed",
                {"state": state, "state_label": LABELS[state]},
                sensitive=False,
            )
            outbox = await one(
                uow.connection, "dispatch_outbox", uow.scope.channel_id, run_id=row["id"]
            )
            if outbox:
                await save(uow, "dispatch_outbox", outbox["id"], {"state": "DONE"})
        else:
            await self.event(
                uow,
                row,
                "tool_status",
                {"state": state, "state_label": LABELS[state]},
                sensitive=False,
            )
        return row

    async def expire(self, uow: UnitOfWork, row: dict[str, Any]) -> bool:
        if row["state"] in TERMINAL:
            return True
        if row["state"] == "CANCEL_REQUESTED":
            await self.transition(uow, row, "CANCELLED")
            return True
        if utcnow() >= row["deadline"]:
            await self.transition(
                uow, row, "TIMED_OUT", error=self.error(row, "RUN_DEADLINE", "运行已超过截止时间")
            )
            return True
        return False

    async def valid_lease(self, uow: UnitOfWork, row: dict[str, Any], lease: Lease) -> None:
        if lease.scope != self.context(row).scope or lease.run_id != row["id"]:
            raise ServiceError("LEASE_INVALID", "租约归属不符", 409)
        stored = await one(uow.connection, "run_leases", lease.scope.channel_id, run_id=row["id"])
        if (
            not stored
            or stored["worker_id"] != lease.worker_id
            or stored["lease_version"] != lease.lease_version
            or stored["expires_at"] <= utcnow()
        ):
            raise ServiceError("LEASE_STALE", "执行租约已失效", 409)

    @staticmethod
    def receipt(row: dict[str, Any]) -> AdmissionReceipt:
        return AdmissionReceipt(
            run_id=row["id"],
            state=row["state"],
            state_label=LABELS[row["state"]],
            created_at=row["created_at"],
            deadline=row["deadline"],
            status_url=f"/api/v1/runs/{row['id']}",
            events_url=f"/api/v1/runs/{row['id']}/events",
        )

    async def release(self, row: dict[str, Any]) -> None:
        context = self.context(row)
        async with transaction(
            self.engine, context.scope, self.keys(context, row["id"], row["conversation_id"])
        ) as uow:
            current = await self.locked_run(uow, row["id"])
            if current["state"] not in TERMINAL or current["resources_released"]:
                return
            await self.budgets.finish_admission(context, row["id"], uow=uow)
            if current["conversation_id"]:
                if self.turns is None:
                    raise unavailable("会话终态事务钩子")
                await self.turns.finish(uow, context, current)
                for occupancy in await rows(
                    uow.connection, "run_occupancies", context.scope.channel_id, run_id=row["id"]
                ):
                    await save(uow, "run_occupancies", occupancy["id"], {"state": "RELEASED"})
            await save(uow, "runs", row["id"], {"resources_released": True})

    async def after_commit(self, row: dict[str, Any]) -> None:
        if row["state"] in TERMINAL:
            self.fault("terminal_committed_before_release")
            await self.release(row)
