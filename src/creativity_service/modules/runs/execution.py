"""带代次的执行租约、有限尝试及恢复判断；外部调用始终在事务外执行。"""

from collections.abc import Awaitable, Callable
from datetime import timedelta
from typing import Any, Literal, cast

from jsonschema import Draft202012Validator

from creativity_service.core.context import TaskEnvelope
from creativity_service.core.contracts import BusinessResult
from creativity_service.core.database import UnitOfWork, transaction
from creativity_service.core.locking import ResourceKey
from creativity_service.core.primitives import ServiceError, digest, new_id, utcnow
from creativity_service.modules.budgets.services import record_cost
from creativity_service.modules.runs.base import RunKernel
from creativity_service.modules.runs.repositories import one, required, rows, save
from creativity_service.modules.runs.schemas import TERMINAL, ExecutionPolicy, Lease, StepPolicy
from creativity_service.modules.usage import repositories as usage_repo
from creativity_service.modules.usage.schemas import AttemptPlan


class ExecutionService(RunKernel):
    async def authorization_error(self, row: dict[str, Any]) -> ServiceError | None:
        try:
            await self.authorization.require(self.context(row), "run:create", row["id"])
            if self.boundary:
                await self.boundary(self.context(row), row)
        except ServiceError as exc:
            if exc.status not in {401, 403, 404, 410}:
                raise
            return exc
        return None

    async def interrupt_attempts(self, uow: UnitOfWork, row: dict[str, Any]) -> bool:
        unknown_model = False
        for attempt in await rows(
            uow.connection, "attempts", uow.scope.channel_id, run_id=row["id"]
        ):
            if attempt["state"] == "STARTED":
                sent = attempt["sent_at"] is not None
                step = await required(
                    uow.connection, "run_steps", uow.scope.channel_id, id=attempt["step_id"]
                )
                read_only = self.step_policy(row, step["node_key"]).read_only
                attempt = await save(
                    uow,
                    "attempts",
                    attempt["id"],
                    {
                        "state": "UNKNOWN" if sent else "FAILED",
                        "finished_at": utcnow(),
                        "retryable": not sent or attempt["kind"] != "model" and read_only,
                        "error": self.error(
                            row,
                            "WORKER_LOST",
                            "执行进程中断，调用结果待核实"
                            if sent
                            else "执行进程中断，尚未发送请求",
                        ),
                    },
                )
                await save(
                    uow, "run_steps", attempt["step_id"], {"state": "UNKNOWN" if sent else "FAILED"}
                )
                if attempt["usage_id"]:
                    await self.ledger.finish_attempt(
                        self.context(row).scope,
                        attempt["id"],
                        "UNKNOWN" if sent else "FAILED",
                        uow=uow,
                    )
                if not sent and attempt["usage_id"]:
                    usage = await usage_repo.required(
                        uow.connection,
                        "usage_records",
                        uow.scope.channel_id,
                        id=attempt["usage_id"],
                    )
                    if usage["state"] == "HELD":
                        await usage_repo.save(
                            uow, "usage_records", usage["id"], {"state": "RELEASED"}
                        )
                        for reservation in await usage_repo.rows(
                            uow.connection,
                            "budget_reservations",
                            uow.scope.channel_id,
                            attempt_id=attempt["id"],
                        ):
                            await usage_repo.save(
                                uow,
                                "budget_reservations",
                                reservation["id"],
                                {"status": "RELEASED"},
                            )
            if attempt["kind"] == "model" and attempt["state"] == "UNKNOWN":
                unknown_model = True
        return unknown_model

    async def expire(self, uow: UnitOfWork, row: dict[str, Any]) -> bool:
        expired = await super().expire(uow, row)
        if expired:
            await self.interrupt_attempts(uow, row)
        return expired

    async def recover_in(self, uow: UnitOfWork, row: dict[str, Any], version: int) -> bool:
        existing = await one(
            uow.connection,
            "run_recoveries",
            uow.scope.channel_id,
            run_id=row["id"],
            lease_version=version,
        )
        if existing:
            return bool(existing["decision"] == "RETRY")
        policy = ExecutionPolicy.model_validate(row["execution_policy"])
        unknown = await self.interrupt_attempts(uow, row)
        reason = (
            "MODEL_RESULT_UNKNOWN"
            if unknown
            else (
                "RECOVERY_LIMIT"
                if row["recovery_count"] >= policy.max_recoveries
                else "LEASE_EXPIRED"
            )
        )
        allowed = reason == "LEASE_EXPIRED"
        await save(
            uow,
            "run_recoveries",
            new_id("recovery"),
            {
                "run_id": row["id"],
                "lease_version": version,
                "decision": "RETRY" if allowed else "STOP",
                "reason": reason,
            },
        )
        if not allowed:
            await self.transition(
                uow,
                row,
                "FAILED",
                error=self.error(
                    row, reason, "模型调用结果待核实" if unknown else "自动恢复次数已达上限"
                ),
            )
            return False
        row.update(
            await save(uow, "runs", row["id"], {"recovery_count": row["recovery_count"] + 1})
        )
        outbox = await required(
            uow.connection, "dispatch_outbox", uow.scope.channel_id, run_id=row["id"]
        )
        await save(
            uow, "dispatch_outbox", outbox["id"], {"state": "PENDING", "next_attempt_at": utcnow()}
        )
        return True

    async def reconcile_run(self, message: TaskEnvelope) -> str:
        original = await self.load(message)
        denied = (
            await self.authorization_error(original) if original["state"] not in TERMINAL else None
        )
        context = self.context(original)
        async with transaction(
            self.engine, context.scope, self.keys(context, message.run_id)
        ) as uow:
            row = await self.locked_run(uow, message.run_id)
            if not await self.expire(uow, row):
                error = denied
                if error is None:
                    try:
                        await self.snapshot(uow, row)
                    except ServiceError as exc:
                        if exc.code not in {"CONTENT_DELETED", "SNAPSHOT_INVALID"}:
                            raise
                        error = exc
                if error:
                    await self.interrupt_attempts(uow, row)
                    await self.transition(
                        uow,
                        row,
                        "FAILED",
                        error=self.error(row, error.code, "当前授权或运行来源已不可用"),
                    )
                elif row["state"] == "RUNNING":
                    lease = await one(
                        uow.connection, "run_leases", message.channel_id, run_id=row["id"]
                    )
                    if not lease or lease["expires_at"] <= utcnow():
                        await self.recover_in(uow, row, lease["lease_version"] if lease else 0)
        await self.after_commit(row)
        return str(row["state"])

    async def claim_lease(self, message: TaskEnvelope, worker_id: str) -> Lease | None:
        if not worker_id or len(worker_id) > 128:
            raise ServiceError("WORKER_INVALID", "执行进程标识无效", 422)
        await self.reconcile_run(message)
        original = await self.load(message)
        if original["state"] in TERMINAL or original["state"] in {
            "WAITING_INPUT",
            "WAITING_APPROVAL",
        }:
            return None
        context = self.context(original)
        result = None
        async with transaction(
            self.engine, context.scope, self.keys(context, message.run_id)
        ) as uow:
            row = await self.locked_run(uow, message.run_id)
            if not await self.expire(uow, row):
                if row["state"] in {"WAITING_INPUT", "WAITING_APPROVAL"}:
                    return None
                await self.snapshot(uow, row)
                old = await one(uow.connection, "run_leases", message.channel_id, run_id=row["id"])
                if old and old["expires_at"] > utcnow():
                    return None
                if row["state"] == "QUEUED":
                    await self.transition(uow, row, "RUNNING")
                result = Lease(
                    scope=context.scope,
                    run_id=row["id"],
                    worker_id=worker_id,
                    lease_version=(old["lease_version"] + 1) if old else 1,
                    expires_at=min(
                        row["deadline"], utcnow() + timedelta(seconds=self.lease_seconds)
                    ),
                )
                await save(
                    uow,
                    "run_leases",
                    old["id"] if old else new_id("lease"),
                    {
                        "run_id": row["id"],
                        "worker_id": worker_id,
                        "lease_version": result.lease_version,
                        "heartbeat_at": utcnow(),
                        "expires_at": result.expires_at,
                    },
                )
                outbox = await required(
                    uow.connection, "dispatch_outbox", message.channel_id, run_id=row["id"]
                )
                await save(uow, "dispatch_outbox", outbox["id"], {"state": "EXECUTING"})
        await self.after_commit(row)
        return result

    async def heartbeat(self, lease: Lease) -> Lease | None:
        original = await self.load(
            TaskEnvelope(channel_id=lease.scope.channel_id, run_id=lease.run_id)
        )
        context = self.context(original)
        result = None
        async with transaction(self.engine, lease.scope, self.keys(context, lease.run_id)) as uow:
            row = await self.locked_run(uow, lease.run_id)
            await self.valid_lease(uow, row, lease)
            if not await self.expire(uow, row):
                expires_at = min(row["deadline"], utcnow() + timedelta(seconds=self.lease_seconds))
                current = await required(
                    uow.connection, "run_leases", lease.scope.channel_id, run_id=lease.run_id
                )
                await save(
                    uow,
                    "run_leases",
                    current["id"],
                    {"heartbeat_at": utcnow(), "expires_at": expires_at},
                )
                result = lease.model_copy(update={"expires_at": expires_at})
        await self.after_commit(row)
        return result

    @staticmethod
    def step_policy(row: dict[str, Any], node_key: str) -> StepPolicy:
        policy = ExecutionPolicy.model_validate(row["execution_policy"])
        for step in policy.steps:
            if step.node_key == node_key:
                return step
        raise ServiceError("STEP_INVALID", "节点不属于冻结执行定义", 422)

    async def before_progress(self, lease: Lease) -> dict[str, Any]:
        original = await self.load(
            TaskEnvelope(channel_id=lease.scope.channel_id, run_id=lease.run_id)
        )
        denied = await self.authorization_error(original)
        if denied:
            await self.reconcile_run(
                TaskEnvelope(channel_id=lease.scope.channel_id, run_id=lease.run_id)
            )
            raise denied
        return original

    async def start_step(
        self, lease: Lease, node_key: str, input_value: dict[str, Any] | None = None
    ) -> dict[str, Any] | None:
        original = await self.before_progress(lease)
        context = self.context(original)
        step = None
        async with transaction(self.engine, lease.scope, self.keys(context, lease.run_id)) as uow:
            row = await self.locked_run(uow, lease.run_id)
            await self.valid_lease(uow, row, lease)
            if not await self.expire(uow, row):
                await self.snapshot(uow, row)
                self.step_policy(row, node_key)
                step = await one(
                    uow.connection,
                    "run_steps",
                    lease.scope.channel_id,
                    run_id=row["id"],
                    node_key=node_key,
                )
                if not step:
                    steps = await rows(
                        uow.connection, "run_steps", lease.scope.channel_id, run_id=row["id"]
                    )
                    step = await save(
                        uow,
                        "run_steps",
                        new_id("step"),
                        {
                            "run_id": row["id"],
                            "node_key": node_key,
                            "state": "RUNNING",
                            "input_ref": await self.content(uow, row, "step_input", input_value),
                            "sequence": max((s["sequence"] for s in steps), default=0) + 1,
                            "attempt_count": 0,
                            "lease_version": lease.lease_version,
                        },
                    )
                    await self.event(
                        uow, row, "step_started", {"step_id": step["id"], "node_key": node_key}
                    )
        await self.after_commit(row)
        return step

    async def start_attempt(
        self,
        lease: Lease,
        node_key: str,
        plan: AttemptPlan | None = None,
        *,
        target_version_id: str | None = None,
        attempt_id: str | None = None,
        provider_credential_id: str | None = None,
    ) -> dict[str, Any] | None:
        original = await self.before_progress(lease)
        context = self.context(original)
        attempt = None
        async with transaction(self.engine, lease.scope, self.keys(context, lease.run_id)) as uow:
            row = await self.locked_run(uow, lease.run_id)
            await self.valid_lease(uow, row, lease)
            if not await self.expire(uow, row):
                snapshot = await self.snapshot(uow, row)
                policy = self.step_policy(row, node_key)
                step = await required(
                    uow.connection,
                    "run_steps",
                    lease.scope.channel_id,
                    run_id=row["id"],
                    node_key=node_key,
                )
                if step["state"] == "SUCCEEDED":
                    raise ServiceError("STEP_FINISHED", "已完成步骤不能重复调用", 409)
                previous = await rows(
                    uow.connection, "attempts", lease.scope.channel_id, step_id=step["id"]
                )
                if previous:
                    latest = max(previous, key=lambda a: a["created_at"])
                    if latest["state"] == "STARTED" or not latest["retryable"]:
                        raise ServiceError(
                            "ATTEMPT_NOT_RETRYABLE", "上次调用尚未结束或不允许重试", 409
                        )
                if step["attempt_count"] >= policy.max_retries + 1:
                    raise ServiceError("RETRY_LIMIT", "步骤重试次数已达上限", 409)
                all_attempts = await rows(
                    uow.connection, "attempts", lease.scope.channel_id, run_id=row["id"]
                )
                limits = ExecutionPolicy.model_validate(row["execution_policy"])
                count = sum(a["kind"] == policy.kind for a in all_attempts)
                if (
                    count
                    >= {
                        "model": limits.max_model_calls,
                        "tool": limits.max_tool_calls,
                        "compute": 1000,
                    }[policy.kind]
                ):
                    raise ServiceError("CALL_LIMIT", "运行调用次数已达上限", 429)
                attempt_id = attempt_id or new_id("attempt")
                if await one(uow.connection, "attempts", lease.scope.channel_id, id=attempt_id):
                    raise ServiceError("ATTEMPT_CONFLICT", "尝试标识已经使用", 409)
                target_id = target_version_id or policy.target_version_id
                target = next(
                    v for v in snapshot.versions if v.version_id == policy.target_version_id
                )
                allowed_targets = {policy.target_version_id}
                if target.resource_type == "model_route" and policy.kind == "model":
                    allowed_targets = {
                        m["model_version_id"]
                        for m in cast(list[dict[str, Any]], target.content["models"])
                    }
                if target_id not in allowed_targets:
                    raise ServiceError("ATTEMPT_TARGET_INVALID", "调用目标不在冻结路由中", 422)
                usage_id = None
                if policy.kind == "model":
                    if (
                        not plan
                        or plan.run_id != row["id"]
                        or plan.agent_id != row["agent_id"]
                        or plan.purpose != row["purpose"]
                    ):
                        raise ServiceError(
                            "BUDGET_ESTIMATE_REQUIRED", "模型调用必须提交本运行的预算候选", 422
                        )
                    target = next(v for v in snapshot.versions if v.version_id == target_id)
                    if plan.model_id != target.resource_id:
                        raise ServiceError(
                            "ATTEMPT_TARGET_INVALID", "预算模型与冻结步骤不一致", 422
                        )
                    await uow.acquire(self.budgets.reservation_keys(context, attempt_id))
                    usages = await usage_repo.rows(
                        uow.connection, "usage_records", lease.scope.channel_id, run_id=row["id"]
                    )
                    tokens, price, upper = await self.budgets.estimate(uow, plan)
                    token_upper = (
                        plan.input_tokens
                        + plan.max_output_tokens
                        + sum(plan.additional_upper_tokens.values())
                    )
                    if (
                        limits.token_limit is not None
                        and token_upper
                        + sum(
                            record_cost(u, "tokens", exposure=True) or 0
                            for u in usages
                            if u["state"] != "RELEASED"
                        )
                        > limits.token_limit
                    ):
                        raise ServiceError("RUN_TOKEN_LIMIT", "运行 Token 额度已达上限", 429)
                    if limits.cost_limit:
                        if (
                            price is None
                            or upper is None
                            or price["currency"] != limits.cost_limit.currency
                        ):
                            raise ServiceError(
                                "BUDGET_PRICE_REQUIRED", "运行金额限制需要有效价格", 429
                            )
                        if (
                            any(
                                u["currency"] != limits.cost_limit.currency
                                for u in usages
                                if u["state"] != "RELEASED"
                            )
                            or upper
                            + sum(
                                record_cost(u, "amount", exposure=True) or 0
                                for u in usages
                                if u["state"] != "RELEASED"
                            )
                            > limits.cost_limit.amount
                        ):
                            raise ServiceError("RUN_COST_LIMIT", "运行费用额度已达上限", 429)
                    await self.budgets.reserve_attempt(
                        context, plan.model_copy(update={"attempt_id": attempt_id}), uow=uow
                    )
                    usage = await usage_repo.required(
                        uow.connection,
                        "usage_records",
                        lease.scope.channel_id,
                        attempt_id=attempt_id,
                    )
                    usage_id = usage["id"]
                attempt = await save(
                    uow,
                    "attempts",
                    attempt_id,
                    {
                        "run_id": row["id"],
                        "step_id": step["id"],
                        "kind": policy.kind,
                        "target_version_id": target_id,
                        "provider_credential_id": provider_credential_id,
                        "state": "STARTED",
                        "started_at": utcnow(),
                        "lease_version": lease.lease_version,
                        "retryable": False,
                        "usage_id": usage_id,
                    },
                )
                await save(
                    uow,
                    "run_steps",
                    step["id"],
                    {
                        "state": "RUNNING",
                        "attempt_count": step["attempt_count"] + 1,
                        "lease_version": lease.lease_version,
                    },
                )
        await self.after_commit(row)
        return attempt

    async def mark_sent(self, lease: Lease, attempt_id: str) -> bool:
        original = await self.before_progress(lease)
        context = self.context(original)
        sent = False
        async with transaction(self.engine, lease.scope, self.keys(context, lease.run_id)) as uow:
            row = await self.locked_run(uow, lease.run_id)
            await self.valid_lease(uow, row, lease)
            if not await self.expire(uow, row):
                await self.snapshot(uow, row)
                attempt = await required(
                    uow.connection,
                    "attempts",
                    lease.scope.channel_id,
                    id=attempt_id,
                    run_id=row["id"],
                    lease_version=lease.lease_version,
                )
                if attempt["sent_at"] or attempt["state"] != "STARTED":
                    raise ServiceError("ATTEMPT_ALREADY_SENT", "此尝试已发送或结束", 409)
                if attempt["usage_id"]:
                    await uow.acquire(self.budgets.reservation_keys(context, attempt_id))
                    usage = await usage_repo.required(
                        uow.connection,
                        "usage_records",
                        lease.scope.channel_id,
                        id=attempt["usage_id"],
                    )
                    if usage["state"] != "HELD":
                        raise ServiceError("BUDGET_RESERVATION_INVALID", "调用预算预占已失效", 409)
                    await usage_repo.save(
                        uow, "usage_records", usage["id"], {"state": "PENDING", "sent_at": utcnow()}
                    )
                    for reservation in await usage_repo.rows(
                        uow.connection,
                        "budget_reservations",
                        lease.scope.channel_id,
                        attempt_id=attempt_id,
                    ):
                        await usage_repo.save(
                            uow, "budget_reservations", reservation["id"], {"status": "PENDING"}
                        )
                await save(uow, "attempts", attempt_id, {"sent_at": utcnow()})
                sent = True
        await self.after_commit(row)
        return sent

    async def finish_attempt(
        self,
        lease: Lease,
        attempt_id: str,
        state: Literal["SUCCEEDED", "FAILED", "UNKNOWN"],
        *,
        source_request_id: str | None = None,
        retryable: bool = False,
        output: dict[str, Any] | None = None,
        error: dict[str, Any] | None = None,
    ) -> bool:
        original = await self.load(
            TaskEnvelope(channel_id=lease.scope.channel_id, run_id=lease.run_id)
        )
        context = self.context(original)
        committed = False
        async with transaction(self.engine, lease.scope, self.keys(context, lease.run_id)) as uow:
            row = await self.locked_run(uow, lease.run_id)
            await self.valid_lease(uow, row, lease)
            if not await self.expire(uow, row):
                await self.guard(uow, row)
                attempt = await required(
                    uow.connection,
                    "attempts",
                    lease.scope.channel_id,
                    run_id=row["id"],
                    id=attempt_id,
                    lease_version=lease.lease_version,
                )
                if attempt["state"] != "STARTED":
                    if (
                        attempt["state"] == state
                        and attempt["source_request_id"] == source_request_id
                    ):
                        return True
                    raise ServiceError("ATTEMPT_STATE_CONFLICT", "尝试已经结束", 409)
                policy = self.step_policy(
                    row,
                    (
                        await required(
                            uow.connection,
                            "run_steps",
                            lease.scope.channel_id,
                            id=attempt["step_id"],
                        )
                    )["node_key"],
                )
                if state == "UNKNOWN":
                    retryable = policy.kind != "model" and policy.read_only
                if state == "SUCCEEDED":
                    if output is None:
                        raise ServiceError(
                            "ATTEMPT_OUTPUT_REQUIRED", "成功尝试必须同时保存返回内容", 422
                        )
                    retryable = False
                    await save(
                        uow,
                        "run_steps",
                        attempt["step_id"],
                        {"output_ref": await self.content(uow, row, "attempt_output", output)},
                    )
                await save(
                    uow,
                    "attempts",
                    attempt_id,
                    {
                        "state": state,
                        "finished_at": utcnow(),
                        "source_request_id": source_request_id,
                        "retryable": retryable,
                        "error": error,
                    },
                )
                if state != "SUCCEEDED":
                    await save(uow, "run_steps", attempt["step_id"], {"state": state})
                if attempt["usage_id"]:
                    await self.ledger.finish_attempt(lease.scope, attempt_id, state, uow=uow)
                await self.event(
                    uow, row, "tool_status", {"attempt_id": attempt_id, "state": state}
                )
                committed = True
        await self.after_commit(row)
        return committed

    async def commit_step(
        self,
        lease: Lease,
        node_key: str,
        output: dict[str, Any],
        *,
        checkpoint_key: str | None = None,
        checkpoint: dict[str, Any] | None = None,
    ) -> dict[str, Any] | None:
        original = await self.before_progress(lease)
        context = self.context(original)
        step = None
        async with transaction(self.engine, lease.scope, self.keys(context, lease.run_id)) as uow:
            row = await self.locked_run(uow, lease.run_id)
            await self.valid_lease(uow, row, lease)
            if not await self.expire(uow, row):
                await self.snapshot(uow, row)
                policy = self.step_policy(row, node_key)
                step = await required(
                    uow.connection,
                    "run_steps",
                    lease.scope.channel_id,
                    run_id=row["id"],
                    node_key=node_key,
                )
                if step["state"] == "SUCCEEDED":
                    old = await required(
                        uow.connection,
                        "run_contents",
                        lease.scope.channel_id,
                        id=step["output_ref"],
                    )
                    if old["payload"] != output:
                        raise ServiceError("STEP_COMMIT_CONFLICT", "步骤结果已提交且内容不同", 409)
                    expected_checkpoint = (
                        digest([lease.scope.model_dump(), row["id"], node_key, checkpoint_key])
                        if checkpoint_key
                        else None
                    )
                    if step["checkpoint_ref"] != expected_checkpoint:
                        raise ServiceError("CHECKPOINT_CONFLICT", "步骤恢复点已经固定", 409)
                    if expected_checkpoint:
                        saved_checkpoint = await required(
                            uow.connection,
                            "checkpoints",
                            lease.scope.channel_id,
                            id=expected_checkpoint,
                        )
                        saved_content = await required(
                            uow.connection,
                            "run_contents",
                            lease.scope.channel_id,
                            id=saved_checkpoint["state_ref"],
                        )
                        if saved_content["payload"] != checkpoint:
                            raise ServiceError("CHECKPOINT_CONFLICT", "相同恢复点内容不同", 409)
                    return step
                attempts = await rows(
                    uow.connection, "attempts", lease.scope.channel_id, step_id=step["id"]
                )
                reconciled = False
                if policy.kind == "tool" and not policy.read_only:
                    intents = await rows(
                        uow.connection,
                        "run_contents",
                        lease.scope.channel_id,
                        run_id=row["id"],
                        kind="write_intent",
                    )
                    reconciled = any(
                        item["payload"]
                        and item["payload"].get("step_id") == step["id"]
                        and item["payload"]["state"] == "SUCCEEDED"
                        and item["payload"]["result"] == output
                        for item in intents
                    )
                if (
                    policy.kind != "compute"
                    and not reconciled
                    and (
                        not attempts
                        or max(attempts, key=lambda a: a["created_at"])["state"] != "SUCCEEDED"
                    )
                ):
                    raise ServiceError("ATTEMPT_REQUIRED", "外部步骤必须先登记成功尝试", 409)
                checkpoint_ref = None
                if checkpoint_key:
                    checkpoint_ref = digest(
                        [lease.scope.model_dump(), row["id"], node_key, checkpoint_key]
                    )
                    old_checkpoint = await one(
                        uow.connection, "checkpoints", lease.scope.channel_id, id=checkpoint_ref
                    )
                    if old_checkpoint:
                        old_content = await required(
                            uow.connection,
                            "run_contents",
                            lease.scope.channel_id,
                            id=old_checkpoint["state_ref"],
                        )
                        if old_content["payload"] != checkpoint:
                            raise ServiceError("CHECKPOINT_CONFLICT", "相同恢复点内容不同", 409)
                    else:
                        await save(
                            uow,
                            "checkpoints",
                            checkpoint_ref,
                            {
                                "run_id": row["id"],
                                "namespace": node_key,
                                "checkpoint_key": checkpoint_key,
                                "lease_version": lease.lease_version,
                                "release_snapshot_id": row["release_snapshot_id"],
                                "state_ref": await self.content(uow, row, "checkpoint", checkpoint),
                                "metadata": {},
                            },
                        )
                step = await save(
                    uow,
                    "run_steps",
                    step["id"],
                    {
                        "state": "SUCCEEDED",
                        "output_ref": await self.content(uow, row, "step_output", output),
                        "checkpoint_ref": checkpoint_ref,
                        "lease_version": lease.lease_version,
                    },
                )
                await self.event(
                    uow, row, "tool_status", {"step_id": step["id"], "state": "SUCCEEDED"}
                )
        await self.after_commit(row)
        return step

    async def append_event(
        self,
        lease: Lease,
        event_type: Literal["text_delta", "tool_status"],
        payload: dict[str, Any],
    ) -> int | None:
        original = await self.before_progress(lease)
        context = self.context(original)
        sequence = None
        async with transaction(
            self.engine, lease.scope, self.keys(context, lease.run_id, original["conversation_id"])
        ) as uow:
            row = await self.locked_run(uow, lease.run_id)
            await self.valid_lease(uow, row, lease)
            if not await self.expire(uow, row):
                await self.guard(uow, row)
                if event_type == "text_delta":
                    previous = (
                        await one(
                            uow.connection,
                            "run_contents",
                            lease.scope.channel_id,
                            id=row["partial_output_ref"],
                        )
                        if row["partial_output_ref"]
                        else None
                    )
                    text = (previous["payload"].get("text", "") if previous else "") + str(
                        payload.get("text", "")
                    )
                    if len(text.encode()) > 1048576:
                        raise ServiceError("OUTPUT_TOO_LARGE", "流式输出超过保存上限", 422)
                    partial = {"text": text, "label": "部分内容", "validated": False}
                    if previous:
                        await save(uow, "run_contents", previous["id"], {"payload": partial})
                    else:
                        row.update(
                            await save(
                                uow,
                                "runs",
                                row["id"],
                                {
                                    "partial_output_ref": await self.content(
                                        uow, row, "partial", partial
                                    )
                                },
                            )
                        )
                await self.event(uow, row, event_type, payload)
                project = getattr(self.turns, "project", None)
                if row["conversation_id"] and project:
                    await project(uow, context, row, event_type, payload)
                sequence = int(row["event_sequence"])
        await self.after_commit(row)
        return sequence

    async def finish_run(
        self,
        lease: Lease,
        state: Literal["SUCCEEDED", "FAILED", "CANCELLED"],
        result: BusinessResult | None = None,
        *,
        failure: ServiceError | None = None,
        commit_result: Callable[[UnitOfWork], Awaitable[BusinessResult]] | None = None,
        commit_keys: list[ResourceKey] | None = None,
    ) -> str:
        """成功回写与终态原子提交；回写钩子只做库内操作，所需锁须预先声明。"""
        if commit_result is not None and (state != "SUCCEEDED" or result is not None):
            raise ValueError("事务回写仅适用于未提供结果的成功结束")
        original = await self.before_progress(lease)
        context = self.context(original)
        keys = [*self.keys(context, lease.run_id), *(commit_keys or [])]
        async with transaction(self.engine, lease.scope, keys) as uow:
            row = await self.locked_run(uow, lease.run_id)
            await self.valid_lease(uow, row, lease)
            if not await self.expire(uow, row):
                snapshot = await self.snapshot(uow, row)
                result_ref = None
                error = None
                if state == "SUCCEEDED":
                    if commit_result is not None:
                        result = await commit_result(uow)
                    full = "business_status" in cast(
                        dict[str, Any], snapshot.output_schema.get("properties", {})
                    )
                    if result is None or not Draft202012Validator(snapshot.output_schema).is_valid(
                        result.model_dump(mode="json") if full else result.data
                    ):
                        raise ServiceError("OUTPUT_SCHEMA_INVALID", "结果不符合冻结输出结构", 422)
                    if any(e.scope != lease.scope for e in result.evidence_refs):
                        raise ServiceError("SCOPE_MISMATCH", "结果证据范围不一致", 403)
                    if any(
                        a["state"] == "STARTED"
                        for a in await rows(
                            uow.connection, "attempts", lease.scope.channel_id, run_id=row["id"]
                        )
                    ):
                        raise ServiceError("ATTEMPT_PENDING", "存在尚未结束的调用尝试", 409)
                    result_ref = await self.content(
                        uow, row, "result", result.model_dump(mode="json")
                    )
                    await self.event(uow, row, "result", result.model_dump(mode="json"))
                elif state == "FAILED":
                    await self.interrupt_attempts(uow, row)
                    error = self.error(
                        row,
                        failure.code if failure else "EXECUTION_FAILED",
                        failure.message if failure else "运行执行失败",
                    )
                await self.transition(uow, row, state, error=error, result_ref=result_ref)
        await self.after_commit(row)
        return str(row["state"])

    async def load_progress(self, lease: Lease, node_key: str) -> dict[str, Any] | None:
        """恢复前读取已保存步骤、返回内容与恢复点，绝不根据队列重投直接再调用。"""
        original = await self.before_progress(lease)
        context = self.context(original)
        progress = None
        async with transaction(self.engine, lease.scope, self.keys(context, lease.run_id)) as uow:
            row = await self.locked_run(uow, lease.run_id)
            await self.valid_lease(uow, row, lease)
            if not await self.expire(uow, row):
                await self.snapshot(uow, row)
                self.step_policy(row, node_key)
                step = await one(
                    uow.connection,
                    "run_steps",
                    lease.scope.channel_id,
                    run_id=row["id"],
                    node_key=node_key,
                )
                if step:
                    output, checkpoint = None, None
                    if step["output_ref"]:
                        output = (
                            await required(
                                uow.connection,
                                "run_contents",
                                lease.scope.channel_id,
                                id=step["output_ref"],
                                run_id=row["id"],
                            )
                        )["payload"]
                    if step["checkpoint_ref"]:
                        point = await required(
                            uow.connection,
                            "checkpoints",
                            lease.scope.channel_id,
                            id=step["checkpoint_ref"],
                            run_id=row["id"],
                        )
                        if point["release_snapshot_id"] != row["release_snapshot_id"]:
                            raise ServiceError("SNAPSHOT_INVALID", "恢复点与冻结快照不一致", 409)
                        checkpoint = (
                            await required(
                                uow.connection,
                                "run_contents",
                                lease.scope.channel_id,
                                id=point["state_ref"],
                                run_id=row["id"],
                            )
                        )["payload"]
                    progress = {"step": step, "output": output, "checkpoint": checkpoint}
        await self.after_commit(row)
        return progress
