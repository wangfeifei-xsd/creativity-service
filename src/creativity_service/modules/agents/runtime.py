"""16 到 11/17 的受理适配：映射切换不改变已解析的执行内容。"""

import json

from creativity_service.core.context import AuthContext
from creativity_service.core.database import UnitOfWork
from creativity_service.core.deletion import ContentRef, DeletionGuard
from creativity_service.core.locking import ResourceKey
from creativity_service.core.primitives import RunInput, ServiceError
from creativity_service.modules.agents.access import locked_require
from creativity_service.modules.agents.repositories import repository, required
from creativity_service.modules.agents.schemas import FrozenExecutionSpec
from creativity_service.modules.agents.services import AgentService
from creativity_service.modules.runs.schemas import ExecutionPolicy, ResolvedDefinition, StepPolicy
from creativity_service.modules.tools.schemas import ToolDefinition


class AgentRunResolver:
    def __init__(self, agents: AgentService) -> None:
        self.agents = agents

    @staticmethod
    def definition(spec: FrozenExecutionSpec) -> ResolvedDefinition:
        config = spec.definition
        steps = []
        for step in config.steps:
            for iteration in range(config.limits.max_iterations):
                steps.append(
                    StepPolicy(
                        node_key=step.key if iteration == 0 else f"{step.key}.i{iteration}",
                        name=step.name,
                        kind=step.kind,
                        target_version_id=(step.dependency or config.bindings.model_route_version)
                        if step.kind == "model"
                        else step.dependency or spec.source_version_id,
                        max_retries=step.max_retries,
                    )
                )
                if step.kind == "model" and config.bindings.embedding_route_version:
                    node = step.key if iteration == 0 else f"{step.key}.i{iteration}"
                    steps.append(
                        StepPolicy(
                            node_key=f"{node}.memory",
                            name="语义记忆检索",
                            kind="model",
                            target_version_id=config.bindings.embedding_route_version,
                            max_retries=0,
                        )
                    )
        for index, version_id in enumerate(config.bindings.tool_versions):
            for call in range(config.limits.max_tool_calls):
                steps.append(
                    StepPolicy(
                        node_key=f"runtime_tool_{index}_{call}",
                        name="调用授权工具",
                        kind="tool",
                        target_version_id=version_id,
                        max_retries=2,
                    )
                )
        # 核查也是冻结的只读工具步骤，计入既有工具调用上限。
        tools = {
            v.version_id: ToolDefinition.model_validate(v.content)
            for v in spec.versions
            if v.resource_type == "tool"
        }
        for index, policy in enumerate(list(steps)):
            tool = tools.get(policy.target_version_id) if policy.kind == "tool" else None
            if not tool or not tool.write_policy:
                continue
            steps[index] = policy.model_copy(
                update={"read_only": False, "max_retries": tool.write_policy.max_submissions - 1}
            )
            for check in range(tool.write_policy.max_checks):
                steps.append(
                    StepPolicy(
                        node_key=f"{policy.node_key}.check.{check}",
                        name="核查外部写入结果",
                        kind="tool",
                        target_version_id=tool.write_policy.status_tool_version_id,
                        max_retries=2,
                    )
                )
        return ResolvedDefinition(
            agent_id=spec.agent_id,
            agent_name=spec.agent_name,
            version_ids=tuple(v.version_id for v in spec.versions),
            input_schema=config.input_schema,
            output_schema=config.output_schema,
            purpose=spec.purpose,
            frozen_spec=spec,
            source_refs=tuple(
                tuple(v) for v in json.loads(spec.payload_json).get("source_refs", [])
            ),
            policy=ExecutionPolicy(
                frozen_spec_id=spec.snapshot_id,
                steps=tuple(steps),
                timeout_seconds=config.limits.deadline_seconds,
                timeout_source="agent-version",
                max_model_calls=config.limits.max_model_rounds,
                max_tool_calls=config.limits.max_tool_calls,
                token_limit=config.limits.token_limit,
                cost_limit=config.limits.cost_limit,
            ),
        )

    async def resolve(self, context: AuthContext, request: RunInput) -> ResolvedDefinition:
        spec = await self.agents.resolve_published(context, request.agent_code)
        if request.conversation_id and not spec.definition.context.conversation_enabled:
            raise ServiceError("CONVERSATION_DISABLED", "此智能体未启用会话", 422)
        return self.definition(spec)

    def keys(self, context: AuthContext, definition: ResolvedDefinition) -> list[ResourceKey]:
        return self.agents.keys(context, definition.agent_id)

    async def validate_in(
        self, uow: UnitOfWork, context: AuthContext, definition: ResolvedDefinition
    ) -> None:
        spec = definition.frozen_spec
        if spec is None or spec.scope != context.scope:
            raise ServiceError("SNAPSHOT_INVALID", "受理缺少同范围的冻结执行定义", 422)
        expected = self.definition(spec)
        if definition.model_dump(exclude={"admission_plan"}) != expected.model_dump(
            exclude={"admission_plan"}
        ):
            raise ServiceError("SNAPSHOT_INVALID", "受理策略或结构不能覆盖冻结执行定义", 422)
        plan = definition.admission_plan
        if (
            plan
            and plan.input_tokens
            + plan.max_output_tokens
            + sum(plan.additional_upper_tokens.values())
            > spec.definition.limits.token_limit
        ):
            raise ServiceError("BUDGET_NOT_EXECUTABLE", "受理估算超过智能体 Token 上限", 422)
        await locked_require(uow, context, "run:create", "agent", spec.agent_id)
        row = await required(uow.connection, context.scope, "agent_candidates", spec.snapshot_id)
        if row["spec"] != spec.model_dump(mode="json"):
            raise ServiceError("SNAPSHOT_INVALID", "执行快照字段不能替换", 422)
        agent = await required(uow.connection, context.scope, "agents", spec.agent_id)
        state = await repository("agent_environment_states", context.scope).get(
            uow.connection, self.agents.mapping_id(context, spec.agent_id)
        )
        if agent["status"] != "ACTIVE" or (state and state["status"] != "ACTIVE"):
            raise ServiceError("AGENT_DISABLED", "智能体已下线或停用", 403)
        await DeletionGuard(context.scope).check(
            uow,
            [
                ContentRef("agent_candidate", spec.snapshot_id),
                ContentRef("agent", spec.agent_id),
                *[ContentRef("version", v.version_id) for v in spec.versions],
            ],
        )
