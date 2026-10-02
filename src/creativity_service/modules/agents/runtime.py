"""16 到 11/17 的受理适配：映射切换不改变已解析的执行内容。"""

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


class AgentRunResolver:
    def __init__(self, agents: AgentService) -> None:
        self.agents = agents

    @staticmethod
    def definition(spec: FrozenExecutionSpec) -> ResolvedDefinition:
        config = spec.definition
        return ResolvedDefinition(
            agent_id=spec.agent_id,
            agent_name=spec.agent_name,
            version_ids=tuple(v.version_id for v in spec.versions),
            input_schema=config.input_schema,
            output_schema=config.output_schema,
            purpose=spec.purpose,
            frozen_spec=spec,
            policy=ExecutionPolicy(
                steps=tuple(
                    StepPolicy(
                        node_key=s.key,
                        kind=s.kind,
                        target_version_id=(s.dependency or config.bindings.model_route_version)
                        if s.kind == "model"
                        else s.dependency or spec.source_version_id,
                        max_retries=s.max_retries,
                    )
                    for s in config.steps
                ),
                timeout_seconds=config.limits.deadline_seconds,
                timeout_source="agent-version",
                max_model_calls=config.limits.max_model_rounds,
                max_tool_calls=max(1, config.limits.max_tool_calls),
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
                ContentRef("agent", spec.agent_id),
                *[ContentRef("version", v.version_id) for v in spec.versions],
            ],
        )
