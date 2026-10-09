"""已确认写意图、稳定源幂等键与只读核查；结果未知绝不直接再次提交。"""

import asyncio
from typing import TYPE_CHECKING, Any

from pydantic import ValidationError

from creativity_service.core.context import AuthContext
from creativity_service.core.contracts import ToolResult
from creativity_service.core.database import transaction
from creativity_service.core.primitives import ServiceError, digest, new_id
from creativity_service.integrations.tools import AdapterResult
from creativity_service.modules.agents.schemas import FrozenExecutionSpec
from creativity_service.modules.runs.repositories import one, rows, save
from creativity_service.modules.runs.schemas import Lease
from creativity_service.modules.runtime.tools import BoundToolPort
from creativity_service.modules.tools.execution import ToolExecutor
from creativity_service.modules.tools.schemas import ToolDefinition, ToolExecution
from creativity_service.modules.tools.validation import validate_json

if TYPE_CHECKING:
    from creativity_service.modules.runtime.engine import RuntimeExecutor


class WriteExecution:
    def __init__(
        self,
        runtime: "RuntimeExecutor",
        context: AuthContext,
        lease: Lease,
        spec: FrozenExecutionSpec,
        node: str,
        call: ToolExecution,
        executor: ToolExecutor,
        port: BoundToolPort,
    ) -> None:
        self.runtime, self.context, self.lease, self.spec = runtime, context, lease, spec
        self.node, self.call, self.executor, self.port = node, call, executor, port
        self.runs = runtime.runs
        self.identifier = digest(
            [context.scope.model_dump(), lease.run_id, node, call.tool_version_id, "write"]
        )
        self.binding = digest(
            [call.model_dump(mode="json"), spec.content_digest, context.principal_id]
        )

    async def store(
        self, change: dict[str, Any] | None = None, *, retry: bool = False
    ) -> dict[str, Any]:
        await self.runs.before_progress(self.lease)
        async with transaction(
            self.runs.engine, self.context.scope, self.runs.keys(self.context, self.lease.run_id)
        ) as uow:
            run = await self.runs.locked_run(uow, self.lease.run_id)
            await self.runs.valid_lease(uow, run, self.lease)
            if await self.runs.expire(uow, run):
                raise ServiceError("RUN_INACTIVE", "运行已停止", 409)
            await self.runs.guard(uow, run)
            old = await one(
                uow.connection, "run_contents", self.context.scope.channel_id, id=self.identifier
            )
            payload = (
                old["payload"]
                if old
                else {
                    "binding": self.binding,
                    "step_id": self.call.step_id,
                    "state": "READY",
                    "submissions": 0,
                    "checks": 0,
                    "result": None,
                }
            )
            if payload["binding"] != self.binding:
                raise ServiceError("CONFIRMATION_STALE", "写入意图的主体、版本或参数已变化", 409)
            if change is not None or old is None:
                payload = {**payload, **(change or {})}
                await save(
                    uow,
                    "run_contents",
                    self.identifier,
                    {"run_id": run["id"], "kind": "write_intent", "payload": payload},
                )
            if retry:
                # 只有来源明确未执行才能开放下一次尝试；原未知结果作为事实保留。
                attempts = await rows(
                    uow.connection,
                    "attempts",
                    self.context.scope.channel_id,
                    step_id=self.call.step_id,
                )
                if attempts:
                    last = max(attempts, key=lambda a: a["created_at"])
                    await save(uow, "attempts", last["id"], {"retryable": True})
            return dict(payload)

    async def wait_check(self, state: dict[str, Any]) -> None:
        await self.runs.suspend(
            self.lease,
            f"{self.node}.verify.{state['checks']}",
            "写入结果待核实",
            {"type": "object"},
            {"提示": "来源尚未确认执行结果，请核查来源状态。"},
            verification=True,
        )

    async def attempt(self, definition: ToolDefinition, automatic: bool) -> ToolResult:
        policy = definition.write_policy
        if policy is None or self.spec.purpose == "evaluation":
            raise ServiceError("TOOL_WRITE_DISABLED", "此运行不允许真实写入", 403)
        tool, _, _, _ = await self.executor.authorize(self.context, self.call)
        state = await self.store()
        if state["state"] == "SUCCEEDED":
            return ToolResult.model_validate(state["result"])
        if state["state"] == "UNKNOWN" and not automatic:
            await self.wait_check(state)
        if state["state"] in {"SENT", "UNKNOWN"}:
            if state["checks"] >= policy.max_checks:
                raise ServiceError(
                    "TOOL_OUTCOME_UNKNOWN", "来源核查次数已达上限，保留未知结果供后续核对", 409
                )
            check = state["checks"]
            try:
                checked = await self.runtime.tool(
                    self.context,
                    self.lease,
                    self.spec,
                    f"{self.node}.check.{check}",
                    policy.status_tool_version_id,
                    {"operation_key": self.identifier},
                )
                query = ToolResult.model_validate(checked)
                outcome = query.data
                if (
                    not isinstance(outcome, dict)
                    or outcome.get("operation_key") != self.identifier
                    or outcome.get("outcome") not in {"SUCCEEDED", "NOT_EXECUTED", "UNKNOWN"}
                ):
                    raise ServiceError("TOOL_RESULT_INVALID", "来源核查结果与写入意图不符", 502)
                if outcome["outcome"] == "SUCCEEDED":
                    raw = AdapterResult.model_validate(outcome["result"])
                    raw = raw.model_copy(
                        update={"evidence_refs": (*raw.evidence_refs, *query.evidence_refs)}
                    )
                    call_id = new_id("tool_call")
                    result = await self.executor.validate_result(
                        self.context, self.call, definition, raw, call_id
                    )
                    await self.executor.authorize(self.context, self.call)
                    await self.runtime.tools.repository.record(
                        self.context,
                        self.call,
                        call_id,
                        tool["id"],
                        "SUCCEEDED",
                        None,
                        result,
                        None,
                        None,
                        {
                            "scope": self.context.scope.model_dump(),
                            "principal_id": self.context.principal_id,
                            "resolution": "source_query",
                        },
                    )
                    await self.store(
                        {
                            "state": "SUCCEEDED",
                            "result": result.model_dump(mode="json"),
                            "checks": check + 1,
                        }
                    )
                    return result
                if outcome["outcome"] == "NOT_EXECUTED":
                    state = await self.store(
                        {
                            "state": "READY",
                            "checks": check + 1,
                            "resolution": query.model_dump(mode="json"),
                        },
                        retry=True,
                    )
                else:
                    state = await self.store({"state": "UNKNOWN", "checks": check + 1})
            except ServiceError as exc:
                if exc.status < 500:
                    raise
                state = await self.store({"state": "UNKNOWN", "checks": check + 1})
            except (ValidationError, KeyError, TypeError):
                # 来源声称成功但缺少有效回执，不等于未执行，禁止因此再次提交。
                state = await self.store({"state": "UNKNOWN", "checks": check + 1})
            if state["state"] == "UNKNOWN":
                if automatic and state["checks"] < policy.max_checks:
                    return await self.check_again(definition, policy.check_delay_ms)
                await self.wait_check(state)
        if state["submissions"] >= policy.max_submissions:
            raise ServiceError("WRITE_SUBMISSION_LIMIT", "写入提交次数已达上限", 409)
        if not automatic:
            await self.runs.suspend(
                self.lease,
                f"{self.node}.approve.{state['submissions']}",
                f"批准执行：{tool['name']}",
                definition.input_schema,
                self.call.arguments,
                approval=True,
            )
        await self.executor.authorize(self.context, self.call)
        if automatic:
            await self.check_preauthorization(definition)
        state = await self.store({"state": "SENT", "submissions": state["submissions"] + 1})
        self.port.operation = {
            "idempotency_key": self.identifier,
            "confirmation_digest": self.binding,
        }
        try:
            result = await self.executor.execute(self.context, self.call)
        except ServiceError:
            state = await self.store({"state": "UNKNOWN"})
            if automatic:
                return await self.check_again(definition, policy.check_delay_ms)
            await self.wait_check(state)
            raise
        await self.store({"state": "SUCCEEDED", "result": result.model_dump(mode="json")})
        return result

    async def check_preauthorization(self, definition: ToolDefinition) -> None:
        # 冻结配置与当前配置必须同时授权；撤销无需等待旧运行结束。
        tool, current = await self.executor.service.repository.resolve(
            self.context, self.call.tool_version_id
        )
        if tool["status"] != "ACTIVE" or current["state"] != "PUBLISHED":
            raise ServiceError("TOOL_PREAUTHORIZATION_DENIED", "预授权工具当前未发布或已停用", 403)
        for checked in (definition, ToolDefinition.model_validate(current["content"])):
            policy = checked.write_policy
            if (
                self.spec.purpose != "production"
                or checked.effect_type != "IDEMPOTENT_WRITE"
                or checked.idempotency_policy != "source_key"
                or self.context.scope.environment not in checked.environments
                or policy is None
                or policy.authorization_mode != "preauthorized"
                or self.spec.agent_code not in policy.allowed_agent_codes
                or self.context.principal_id not in policy.allowed_principal_ids
                or not policy.argument_constraints
            ):
                raise ServiceError(
                    "TOOL_PREAUTHORIZATION_DENIED", "当前运行不在有效的工具预授权范围内", 403
                )
            validate_json(self.call.arguments, policy.argument_constraints, "TOOL_INPUT_INVALID")

    async def check_again(self, definition: ToolDefinition, delay_ms: int) -> ToolResult:
        # 核查使用原写意图与独立只读步骤；限额持久化，Worker 恢复不会重置次数。
        await asyncio.sleep(delay_ms / 1000)
        return await self.execute(definition)

    async def execute(self, definition: ToolDefinition) -> ToolResult:
        policy = definition.write_policy
        automatic = bool(
            policy
            and policy.authorization_mode == "preauthorized"
            and self.spec.purpose == "production"
        )
        if automatic:
            await self.check_preauthorization(definition)
        return await self.attempt(definition, automatic)
