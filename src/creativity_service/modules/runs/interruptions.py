"""显式暂停与一次性确认；恢复只重新派发原运行，不改变快照或截止时间。"""

from typing import Any, Literal

from jsonschema import Draft202012Validator
from pydantic import Field

from creativity_service.core.context import AuthContext, TaskEnvelope
from creativity_service.core.database import transaction
from creativity_service.core.primitives import (
    Contract,
    Digest,
    Identifier,
    Revision,
    ServiceError,
    digest,
    utcnow,
)
from creativity_service.modules.iam.reading import (
    read_actions,
    read_policy,
    require_action,
    resource_state,
)
from creativity_service.modules.runs.execution import ExecutionService
from creativity_service.modules.runs.repositories import one, required, save
from creativity_service.modules.runs.schemas import AdmissionReceipt, Lease

WAITING = {"WAITING_INPUT", "WAITING_APPROVAL"}


class RuntimeSuspended(Exception):
    """暂停事务已经提交，执行器应释放 Worker，不写失败终态。"""


class ResumeInput(Contract):
    interruption_id: Identifier
    revision: Revision
    confirmation_digest: Digest
    decision: Literal["respond", "approve", "reject", "verify"]
    input: dict[str, Any] = Field(default_factory=dict)
    idempotency_key: Identifier


class InterruptionView(Contract):
    interruption_id: str
    revision: int
    name: str
    state: str
    state_label: str
    confirmation_digest: str
    input_schema: dict[str, Any]
    proposed_input: dict[str, Any]
    can_respond: bool
    can_approve: bool
    can_verify: bool


class InterruptionOperations(ExecutionService):
    async def suspend(
        self,
        lease: Lease,
        node: str,
        name: str,
        schema: dict[str, Any],
        values: dict[str, Any],
        *,
        approval: bool = False,
        verification: bool = False,
    ) -> dict[str, Any]:
        original = await self.before_progress(lease)
        context = self.context(original)
        identifier = digest([context.scope.model_dump(), lease.run_id, node, "interrupt"])
        binding = digest(
            [
                context.scope.model_dump(),
                lease.run_id,
                node,
                original["release_snapshot_id"],
                schema,
                values,
                approval,
                verification,
            ]
        )
        result: dict[str, Any] | None = None
        async with transaction(self.engine, context.scope, self.keys(context, lease.run_id)) as uow:
            row = await self.locked_run(uow, lease.run_id)
            await self.valid_lease(uow, row, lease)
            await self.guard(uow, row)
            stored = await one(
                uow.connection, "run_contents", context.scope.channel_id, id=identifier
            )
            if stored:
                payload = stored["payload"]
                if payload["binding"] != binding:
                    raise ServiceError(
                        "CONFIRMATION_STALE", "确认对象或参数已变化，不能继续执行", 409
                    )
                if payload["state"] == "RESOLVED":
                    result = payload["response"]
            if result is None:
                if stored is None:
                    await save(
                        uow,
                        "run_contents",
                        identifier,
                        {
                            "run_id": row["id"],
                            "kind": "interruption",
                            "payload": {
                                "node": node,
                                "name": name,
                                "schema": schema,
                                "values": values,
                                "approval": approval,
                                "verification": verification,
                                "binding": binding,
                                "state": "WAITING",
                                "response": None,
                            },
                        },
                    )
                await self.transition(uow, row, "WAITING_APPROVAL" if approval else "WAITING_INPUT")
                previous = await required(
                    uow.connection, "run_leases", context.scope.channel_id, run_id=row["id"]
                )
                await save(
                    uow,
                    "run_leases",
                    previous["id"],
                    {"expires_at": utcnow(), "lease_version": previous["lease_version"] + 1},
                )
                outbox = await required(
                    uow.connection, "dispatch_outbox", context.scope.channel_id, run_id=row["id"]
                )
                await save(uow, "dispatch_outbox", outbox["id"], {"state": "SUSPENDED"})
        if result is None:
            raise RuntimeSuspended()
        return result

    async def interruption(self, context: AuthContext, run_id: str) -> InterruptionView | None:
        context = await self.access_context(context, run_id)
        policy = await read_policy(self.authorization, context)
        permissions = await read_actions(
            self.authorization,
            context,
            "run",
            run_id,
            ["run:content", "run:create", "run:approve"],
            policy=policy,
            state=resource_state(context, "run", {"id": run_id}),
        )
        require_action(permissions, "run:content")
        allowed = {action: action in permissions for action in ("run:create", "run:approve")}
        async with transaction(self.engine, context.scope, self.keys(context, run_id)) as uow:
            row = await self.locked_run(uow, run_id)
            if context.client_id and context.client_id != row["client_id"]:
                raise ServiceError("NOT_FOUND", "运行记录不存在", 404)
            await self.guard(uow, row)
            if row["state"] not in WAITING:
                return None
            from creativity_service.modules.runs.repositories import rows

            pending = [
                item
                for item in await rows(
                    uow.connection,
                    "run_contents",
                    context.scope.channel_id,
                    run_id=run_id,
                    kind="interruption",
                )
                if item["payload"]["state"] == "WAITING"
            ]
            if len(pending) != 1:
                raise ServiceError("INTERRUPTION_INVALID", "运行暂停记录不一致", 503)
            stored = pending[0]
            payload = stored["payload"]
            return InterruptionView(
                interruption_id=stored["id"],
                revision=stored["revision"],
                name=payload["name"],
                state=row["state"],
                state_label="等待审批" if payload["approval"] else "等待补充",
                confirmation_digest=payload["binding"],
                input_schema=payload["schema"],
                proposed_input=payload["values"],
                can_respond=allowed["run:create"]
                and not payload["approval"]
                and not payload.get("verification"),
                can_approve=allowed["run:approve"] and payload["approval"],
                can_verify=allowed["run:create"] and bool(payload.get("verification")),
            )

    async def resume(
        self, context: AuthContext, run_id: str, body: ResumeInput
    ) -> AdmissionReceipt:
        context = await self.access_context(context, run_id)
        await self.authorization.require(context, "run:content", run_id)
        await self.authorization.require(
            context,
            "run:create" if body.decision in {"respond", "verify"} else "run:approve",
            run_id,
        )
        original = await self.load(TaskEnvelope(channel_id=context.scope.channel_id, run_id=run_id))
        denied = await self.authorization_error(original)
        if denied:
            raise denied
        fingerprint = digest([body.model_dump(mode="json"), context.principal_id])
        async with transaction(self.engine, context.scope, self.keys(context, run_id)) as uow:
            row = await self.locked_run(uow, run_id)
            if context.client_id and context.client_id != row["client_id"]:
                raise ServiceError("NOT_FOUND", "运行记录不存在", 404)
            await self.guard(uow, row)
            stored = await required(
                uow.connection, "run_contents", context.scope.channel_id, id=body.interruption_id
            )
            if stored["run_id"] != run_id or stored["kind"] != "interruption":
                raise ServiceError("NOT_FOUND", "暂停记录不存在", 404)
            payload = stored["payload"]
            if payload["state"] == "RESOLVED":
                if payload.get("request_digest") != fingerprint:
                    raise ServiceError(
                        "IDEMPOTENCY_CONFLICT", "暂停已处理，不能提交不同恢复内容", 409
                    )
                return self.receipt(row)
            if row["state"] not in WAITING or row["deadline"] <= utcnow():
                raise ServiceError("RUN_INACTIVE", "运行已结束或超过恢复期限", 409)
            if (
                stored["revision"] != body.revision
                or payload["binding"] != body.confirmation_digest
            ):
                raise ServiceError("CONFIRMATION_STALE", "确认凭据已失效，请刷新", 409)
            if payload["approval"] != (body.decision in {"approve", "reject"}):
                raise ServiceError("CONFIRMATION_INVALID", "恢复操作与暂停类型不符", 422)
            if bool(payload.get("verification")) != (body.decision == "verify"):
                raise ServiceError("CONFIRMATION_INVALID", "此暂停只能查询来源状态", 422)
            if (payload["approval"] or payload.get("verification")) and body.input:
                raise ServiceError("CONFIRMATION_INVALID", "审批不能修改待执行参数", 422)
            response = (
                payload["values"]
                if payload["approval"] or payload.get("verification")
                else body.input
            )
            if body.decision != "reject" and not Draft202012Validator(payload["schema"]).is_valid(
                response
            ):
                raise ServiceError("RESUME_INPUT_INVALID", "补充内容不符合输入结构", 422)
            await save(
                uow,
                "run_contents",
                stored["id"],
                {
                    "payload": {
                        **payload,
                        "state": "RESOLVED",
                        "response": response,
                        "decision": body.decision,
                        "request_digest": fingerprint,
                        "resolved_by": context.principal_id,
                        "resolved_at": utcnow().isoformat(),
                    }
                },
            )
            await self.transition(uow, row, "CANCELLED" if body.decision == "reject" else "QUEUED")
            outbox = await required(
                uow.connection, "dispatch_outbox", context.scope.channel_id, run_id=run_id
            )
            await save(
                uow,
                "dispatch_outbox",
                outbox["id"],
                {
                    "state": "DONE" if body.decision == "reject" else "PENDING",
                    "next_attempt_at": utcnow(),
                },
            )
        await self.after_commit(row)
        return self.receipt(row)
