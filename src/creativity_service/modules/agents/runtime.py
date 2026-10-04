"""16 到 11/17 的受理适配：映射切换不改变已解析的执行内容。"""

import json
from typing import Any

from creativity_service.core.context import AuthContext
from creativity_service.core.database import UnitOfWork
from creativity_service.core.database.inserts import InsertBatch
from creativity_service.core.database.reading import read_connection
from creativity_service.core.deletion import ContentRef, DeletionGuard
from creativity_service.core.locking import ResourceKey, read_key, record_key
from creativity_service.core.primitives import (
    RunInput,
    ServiceError,
    canonical_json,
    digest,
    new_id,
    utcnow,
)
from creativity_service.core.versioning import version_view
from creativity_service.modules.agents.access import locked_require
from creativity_service.modules.agents.repositories import dependency_rows, repository, required
from creativity_service.modules.agents.schemas import AgentDefinition, FrozenExecutionSpec
from creativity_service.modules.agents.services import AgentService
from creativity_service.modules.releases.checks import published_dependencies_match
from creativity_service.modules.runs.schemas import ExecutionPolicy, ResolvedDefinition, StepPolicy
from creativity_service.modules.tools.schemas import ToolDefinition
from creativity_service.modules.usage.repositories import budget_configuration_key


class AgentRunResolver:
    def __init__(self, agents: AgentService) -> None:
        self.agents = agents

    @staticmethod
    def definition(spec: FrozenExecutionSpec) -> ResolvedDefinition:
        config = spec.definition
        versions = spec.versions
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
            for v in versions
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
            version_ids=tuple(v.version_id for v in versions),
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

    async def prepare(
        self, context: AuthContext, request: RunInput
    ) -> tuple[ResolvedDefinition, "PreparedAgentResolver"]:
        """事务外只准备锁集合和不可变内容；实时校验、冻结与受理共用一个提交。"""
        async with read_connection(self.agents.engine) as connection:
            found = await repository("agents", context.scope).find(
                connection, agent_code=request.agent_code
            )
            if len(found) != 1:
                raise ServiceError("NOT_FOUND", "智能体不存在", 404)
            agent = found[0]
            mapping_id = self.agents.mapping_id(context, agent["id"])
            mapping = await repository("release_mappings", context.scope).get(
                connection, mapping_id
            )
            if mapping is None:
                raise ServiceError("AGENT_NOT_RELEASED", "智能体尚未发布到当前环境", 409)
            version = await required(
                connection, context.scope, "resource_versions", mapping["version_id"]
            )
            config = AgentDefinition.model_validate(version["content"])
            dependencies = await dependency_rows(connection, context.scope, config.bindings.ids())
        if request.conversation_id and not config.context.conversation_enabled:
            raise ServiceError("CONVERSATION_DISABLED", "此智能体未启用会话", 422)
        identifier = new_id("candidate")
        spec = FrozenExecutionSpec(
            snapshot_id=identifier,
            scope=context.scope,
            agent_id=agent["id"],
            agent_code=agent["agent_code"],
            agent_name=agent["name"],
            source_version_id=version["id"],
            source_revision=version["revision"],
            purpose="production",
            content_digest=version["content_digest"],
            dependencies_digest=version["dependencies_digest"],
            candidate_digest=digest([version["content_digest"], version["dependencies_digest"]]),
            payload_json=canonical_json(
                {
                    "definition": version["content"],
                    "versions": [
                        version_view(row).model_dump(mode="json")
                        for row in [version, *dependencies]
                    ],
                }
            ).decode(),
            captured_at=utcnow(),
        )
        dependencies_keys = await self.agents.dependency_keys(context, config, loaded=dependencies)
        definition = self.definition(spec)
        return definition, PreparedAgentResolver(
            self,
            spec,
            mapping,
            dependencies,
            dependencies_keys,
            definition.model_dump(exclude={"admission_plan"}),
        )

    def keys(self, context: AuthContext, definition: ResolvedDefinition) -> list[ResourceKey]:
        from creativity_service.core.locking import read_key

        # 最终受理只复核候选及可变授权；预算在事务末尾独立声明写锁。
        return [
            read_key(key)
            for key in self.agents.keys(context, definition.agent_id)
            if key.resource_type != "usage-ledger"
        ]

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


class PreparedAgentResolver:
    """一次请求的准备数据，不能作为跨请求或跨事务的授权缓存。"""

    def __init__(
        self,
        owner: AgentRunResolver,
        spec: FrozenExecutionSpec,
        mapping: dict[str, Any],
        dependencies: list[dict[str, Any]],
        keys: list[ResourceKey],
        expected_definition: dict[str, Any],
    ) -> None:
        self.owner, self.spec, self.mapping = owner, spec, mapping
        self.dependencies, self.dependency_keys = dependencies, keys
        self.expected_definition = expected_definition

    def keys(self, context: AuthContext, definition: ResolvedDefinition) -> list[ResourceKey]:
        agents, spec = self.owner.agents, self.spec
        reads = (
            agents.keys(context, spec.agent_id)
            + self.dependency_keys
            + [
                record_key(context.scope.channel_id, "resource_versions", spec.source_version_id),
                record_key(context.scope.channel_id, "release_mappings", self.mapping["id"]),
            ]
        )
        # 先并行校验配置和身份，到预算读取前才取得渠道账本锁。
        return [read_key(key) for key in reads if key.resource_type != "usage-ledger"] + [
            read_key(budget_configuration_key(context.scope.channel_id)),
            record_key(context.scope.channel_id, "agent_candidates", spec.snapshot_id),
            *agents.candidate_keys(
                context,
                spec.snapshot_id,
                spec.agent_id,
                spec.source_version_id,
                self.dependency_keys,
            ),
        ]

    async def validate_batch_in(
        self,
        uow: UnitOfWork,
        context: AuthContext,
        definition: ResolvedDefinition,
        pending: InsertBatch,
    ) -> ResolvedDefinition:
        return await self.validate_in(uow, context, definition, pending=pending)

    async def validate_in(
        self,
        uow: UnitOfWork,
        context: AuthContext,
        definition: ResolvedDefinition,
        *,
        pending: InsertBatch | None = None,
    ) -> ResolvedDefinition:
        agents, spec = self.owner.agents, self.spec
        if (
            spec.scope != context.scope
            or definition.model_dump(exclude={"admission_plan"}) != self.expected_definition
        ):
            raise ServiceError("SNAPSHOT_INVALID", "受理准备数据与执行策略不一致", 422)
        mapping = await repository("release_mappings", context.scope).get(
            uow.connection, self.mapping["id"]
        )
        if mapping != self.mapping:
            raise ServiceError("REVISION_CONFLICT", "环境映射已变更，请重新受理", 409)
        agent, version = await agents.locked_version(
            uow, context, spec.agent_id, spec.source_version_id, spec.source_revision, "run:create"
        )
        if version["state"] != "PUBLISHED" or version["content_digest"] != spec.content_digest:
            raise ServiceError("AGENT_NOT_RELEASED", "正式运行只能使用当前发布的不可变版本", 409)
        # 跨越事务边界后按本批版本重新读取；只准备锁集合，不信任事务外的可变状态。
        current = await repository("resource_versions", context.scope).get_many(
            uow.connection, [row["id"] for row in self.dependencies]
        )
        if any(current.get(row["id"]) != row for row in self.dependencies):
            raise ServiceError("REVISION_CONFLICT", "依赖版本已变化，请重新受理", 409)
        validation, dependencies, manifest = await agents.inspect_in(
            uow,
            context,
            agent,
            version,
            "production",
            require_evaluation=False,
            loaded_dependencies=[current[row["id"]] for row in self.dependencies],
            defer_budget=True,
        )
        agents.require_valid(validation)
        plan = definition.admission_plan
        if plan and (
            plan.input_tokens + plan.max_output_tokens + sum(plan.additional_upper_tokens.values())
            > spec.definition.limits.token_limit
        ):
            raise ServiceError("BUDGET_NOT_EXECUTABLE", "受理估算超过智能体 Token 上限", 422)
        if not await published_dependencies_match(uow, version, manifest):
            raise ServiceError("DEPENDENCY_INVALID", "发布依赖或策略已变化，需要重新发布", 409)
        frozen = await agents.store_candidate(
            uow,
            context,
            spec.snapshot_id,
            agent,
            version,
            "production",
            validation,
            dependencies,
            manifest,
            pending=pending,
        )
        # 版本与依赖已逐一复核；沿用本次请求已解析的步骤和结构，仅替换最终冻结证据及名称。
        return definition.model_copy(
            update={"frozen_spec": frozen, "agent_name": frozen.agent_name}
        )
