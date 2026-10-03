"""工具执行端口绑定当前有效租约；工具服务的每次重试均单独占用次数。"""

from typing import Any

from creativity_service.core.context import AuthContext
from creativity_service.core.contracts import Attempt, ToolResult
from creativity_service.core.primitives import ServiceError, digest
from creativity_service.modules.agents.schemas import FrozenExecutionSpec
from creativity_service.modules.iam.authorization import IamAuthorization
from creativity_service.modules.runs.repositories import required
from creativity_service.modules.runs.schemas import Lease
from creativity_service.modules.runs.services import RunService
from creativity_service.modules.tools.schemas import (
    RunToolGrant,
    ToolExecution,
    ToolTestInput,
    ToolTestResult,
)


class BoundToolPort:
    def __init__(
        self,
        runs: RunService,
        lease: Lease,
        spec: FrozenExecutionSpec,
        authorization: IamAuthorization,
    ) -> None:
        self.runs, self.lease, self.spec, self.authorization = runs, lease, spec, authorization
        self.operation: dict[str, str] | None = None

    async def step(self, context: AuthContext, run_id: str, step_id: str) -> dict[str, Any]:
        if context.scope != self.lease.scope or run_id != self.lease.run_id:
            raise ServiceError("TOOL_FORBIDDEN", "工具调用归属不符", 403)
        run = await self.runs.before_progress(self.lease)
        if run["state"] != "RUNNING":
            raise ServiceError("RUN_INACTIVE", "运行已停止", 409)
        async with self.runs.engine.connect() as connection:
            return await required(
                connection, "run_steps", context.scope.channel_id, id=step_id, run_id=run_id
            )

    async def authorize_call(self, context: AuthContext, call: ToolExecution) -> RunToolGrant:
        step = await self.step(context, call.run_id, call.step_id)
        policy = self.runs.step_policy(
            await self.runs.before_progress(self.lease), step["node_key"]
        )
        if policy.kind != "tool" or policy.target_version_id != call.tool_version_id:
            raise ServiceError("TOOL_FORBIDDEN", "工具不是此步骤的冻结依赖", 403)
        version = next(
            (v for v in self.spec.versions if v.version_id == call.tool_version_id), None
        )
        if version is None or version.resource_type != "tool":
            raise ServiceError("TOOL_FORBIDDEN", "工具不在运行快照中", 403)
        decision = await self.authorization.check(
            context, "run:create", "tool", version.resource_id
        )
        return RunToolGrant(
            scope=context.scope,
            run_id=call.run_id,
            step_id=call.step_id,
            agent_version_id=self.spec.source_version_id,
            tool_version_ids=frozenset(self.spec.definition.bindings.tool_versions),
            allowed_actions=frozenset(decision.actions),
            purpose=self.spec.purpose,
            authorization_revision=digest(sorted(decision.actions)),
            draft_revisions={
                v.version_id: v.draft_revision
                for v in self.spec.versions
                if v.draft_revision is not None
            },
        )

    async def start_attempt(self, context: AuthContext, attempt: Attempt) -> None:
        step = await self.step(context, attempt.run_id, attempt.step_id)
        value = await self.runs.start_attempt(
            self.lease,
            step["node_key"],
            target_version_id=attempt.target_version_id,
            attempt_id=attempt.attempt_id,
        )
        if value is None or not await self.runs.mark_sent(self.lease, attempt.attempt_id):
            raise ServiceError("RUN_INACTIVE", "运行已停止", 409)

    async def finish_attempt(self, context: AuthContext, attempt: Attempt) -> None:
        await self.finish_result(context, attempt, None)

    async def finish_result(
        self, context: AuthContext, attempt: Attempt, result: ToolResult | None
    ) -> None:
        if context.scope != self.lease.scope or attempt.run_id != self.lease.run_id:
            raise ServiceError("TOOL_FORBIDDEN", "工具结果归属不符", 403)
        if attempt.state == "STARTED":
            raise ServiceError("ATTEMPT_PENDING", "工具调用尚未结束", 409)
        await self.runs.finish_attempt(
            self.lease,
            attempt.attempt_id,
            attempt.state,
            source_request_id=attempt.source_request_id,
            retryable=bool(attempt.error and attempt.error.retryable),
            output=result.model_dump(mode="json") if result else None,
            error=attempt.error.model_dump(mode="json") if attempt.error else None,
        )

    async def create_debug_run(
        self, context: AuthContext, version_id: str, body: ToolTestInput
    ) -> ToolTestResult:
        raise ServiceError("TOOL_FORBIDDEN", "运行中的工具不能创建新的调试任务", 403)
