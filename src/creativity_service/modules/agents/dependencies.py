"""锁下解析完整依赖、当前授权和模型证据，禁止技能扩展工具白名单。"""

from typing import Any

from jsonschema import Draft202012Validator

from creativity_service.core.context import AuthContext
from creativity_service.core.database import UnitOfWork
from creativity_service.core.deletion import ContentRef, DeletionGuard
from creativity_service.core.locking import record_key
from creativity_service.core.primitives import ServiceError, digest
from creativity_service.core.versioning import version_view
from creativity_service.modules.agents.access import locked_require
from creativity_service.modules.agents.repositories import (
    RESOURCE_TABLES,
    dependency_rows,
    repository,
    required,
)
from creativity_service.modules.agents.schemas import AgentDefinition, Purpose
from creativity_service.modules.agents.validation import compatible, schema_field
from creativity_service.modules.mcp.bindings import require_current_binding
from creativity_service.modules.memory.base import MemoryKernel
from creativity_service.modules.memory.validation import validate_policy
from creativity_service.modules.models.policy import (
    PROTOCOLS,
    configuration_digest,
    require_capabilities,
)
from creativity_service.modules.models.schemas import FrozenModel
from creativity_service.modules.prompts.schemas import PromptContent
from creativity_service.modules.skills.schemas import SkillDefinition
from creativity_service.modules.skills.services import SkillService, SkillVersionValidator
from creativity_service.modules.tools.schemas import ToolDefinition
from creativity_service.modules.tools.services import ToolService
from creativity_service.modules.tools.validation import validate_definition


class DependencyResolver:
    def __init__(self, tools: ToolService, skills: SkillService) -> None:
        self.tools, self.skills = tools, skills

    async def resolve(
        self,
        uow: UnitOfWork,
        context: AuthContext,
        agent_id: str,
        definition: AgentDefinition,
        purpose: Purpose,
    ) -> tuple[list[dict[str, Any]], dict[str, Any], list[FrozenModel]]:
        bindings, scope = definition.bindings, context.scope
        if not bindings.prompt_version or not bindings.model_route_version:
            raise ServiceError("DEPENDENCY_INVALID", "请选择提示词版本和模型路由版本", 422)
        rows = await dependency_rows(uow.connection, scope, bindings.ids())
        indexed = {r["id"]: r for r in rows}
        types = {
            bindings.prompt_version: "prompt",
            bindings.model_route_version: "model_route",
            **dict.fromkeys(bindings.tool_versions, "tool"),
            **dict.fromkeys(bindings.skill_versions, "skill"),
        }
        if bindings.embedding_route_version:
            types[bindings.embedding_route_version] = "model_route"
            if (
                not definition.context.memory_policy
                or not definition.context.memory_policy.read_enabled
            ):
                raise ServiceError("DEPENDENCY_INVALID", "语义检索需要启用记忆读取", 422)
        for identifier, kind in types.items():
            if indexed[identifier]["resource_type"] != kind:
                raise ServiceError("DEPENDENCY_INVALID", "依赖版本类型与选择位置不符", 422)
        capabilities = {"text", "structured_output"}
        if bindings.tool_versions:
            capabilities.add("tools")
        for row in rows:
            kind = row["resource_type"]
            if kind not in RESOURCE_TABLES or kind == "agent":
                raise ServiceError("DEPENDENCY_INVALID", "依赖清单包含不支持的资源类型", 422)
            if row["state"] not in {"DRAFT", "PUBLISHED"} or (
                purpose == "production" and row["state"] != "PUBLISHED"
            ):
                raise ServiceError("DEPENDENCY_INVALID", "正式发布依赖须为同渠道已发布版本", 422)
            if row["content_digest"] != digest(
                {"content": row["content"], "output_schema": row["output_schema"]}
            ):
                raise ServiceError("DEPENDENCY_INVALID", "依赖内容摘要不一致", 409)
            resource = await required(
                uow.connection, scope, RESOURCE_TABLES[kind], row["resource_id"]
            )
            if resource.get("status", "ACTIVE") != "ACTIVE":
                raise ServiceError(
                    "DEPENDENCY_INVALID", f"{resource.get('name', '依赖资源')}已停用", 422
                )
            await DeletionGuard(scope).check(
                uow, [ContentRef(kind, resource["id"]), ContentRef("version", row["id"])]
            )
            if kind not in {"model_connection", "model_route"}:
                await locked_require(uow, context, "run:create", kind, resource["id"])
            if row["state"] == "DRAFT":
                await locked_require(uow, context, "version:edit", kind, resource["id"])
            if kind == "tool":
                if row["id"] not in bindings.tool_versions:
                    raise ServiceError(
                        "DEPENDENCY_INVALID", "技能所需工具不在智能体工具白名单中", 422
                    )
                tool = ToolDefinition.model_validate(row["content"])
                validate_definition(tool)
                if (
                    scope.environment not in tool.environments
                    or scope.data_scope_id not in tool.allowed_data_domains
                ):
                    raise ServiceError("DEPENDENCY_INVALID", "工具未授权当前环境或数据域", 422)
                if tool.write_policy:
                    status_id = tool.write_policy.status_tool_version_id
                    if status_id not in bindings.tool_versions or status_id not in indexed:
                        raise ServiceError(
                            "DEPENDENCY_INVALID", "写工具的核查工具须在固定白名单中", 422
                        )
                    status = ToolDefinition.model_validate(indexed[status_id]["content"])
                    if status.effect_type != "READ_ONLY" or not Draft202012Validator(
                        status.input_schema
                    ).is_valid({"operation_key": "0" * 64}):
                        raise ServiceError(
                            "DEPENDENCY_INVALID", "核查工具须只读并接受 operation_key", 422
                        )
                for action in tool.required_scopes:
                    await locked_require(uow, context, action, "tool", resource["id"])
                self.tools.registry.validate(scope, tool, resource["source_type"], executable=True)
                if resource["source_type"] == "mcp":
                    await self.check_mcp(uow, context, tool)
                for step in definition.steps:
                    if (
                        step.kind == "tool"
                        and step.dependency == row["id"]
                        and (
                            not compatible(step.input_schema, tool.input_schema)
                            or not compatible(tool.output_schema, step.output_schema)
                            or step.timeout_seconds < tool.timeout_seconds
                        )
                    ):
                        raise ServiceError(
                            "FLOW_INVALID", f"{step.name}的输入、输出或超时与工具契约不符", 422
                        )
            if kind == "skill":
                skill = SkillDefinition.model_validate(row["content"])
                loading = next(
                    (s for s in bindings.skill_loading if s.version_id == row["id"]), None
                )
                if loading:
                    files = {f.relative_path: f for f in skill.files}
                    if any(
                        path not in files or not files[path].loadable
                        for path in loading.selected_files
                    ):
                        raise ServiceError("DEPENDENCY_INVALID", "技能所选资料缺失或不可加载", 422)
                if skill.allowed_agents and agent_id not in skill.allowed_agents:
                    raise ServiceError("DEPENDENCY_INVALID", "技能未授权此智能体", 403)
                if not set(skill.required_tool_versions) <= set(bindings.tool_versions):
                    raise ServiceError("DEPENDENCY_INVALID", "技能不能扩大智能体工具白名单", 422)
                await SkillVersionValidator(self.skills).validate(
                    uow, context, version_view(row), "release"
                )
                capabilities.update(skill.required_model_capabilities)
                for variable in skill.input_variables:
                    source = schema_field(definition.input_schema, variable.name)
                    if variable.required and (
                        not source or source.get("type") != variable.value_type
                    ):
                        raise ServiceError("FLOW_INVALID", "技能必填变量缺少兼容的运行输入", 422)
        prompt = PromptContent.model_validate(indexed[bindings.prompt_version]["content"])
        for prompt_variable in prompt.variables:
            if prompt_variable.source == "input" and prompt_variable.required:
                source = schema_field(definition.input_schema, prompt_variable.name)
                if source is None or source.get("type") != prompt_variable.type:
                    raise ServiceError(
                        "FLOW_INVALID",
                        f"提示词变量“{prompt_variable.display_name}”缺少兼容输入",
                        422,
                    )
            if prompt_variable.source == "tool" and not bindings.tool_versions:
                raise ServiceError("DEPENDENCY_INVALID", "提示词声明工具来源但未选择工具", 422)
            if prompt_variable.source == "memory" and not definition.context.memory_policy:
                raise ServiceError("DEPENDENCY_INVALID", "提示词声明记忆来源但未配置记忆策略", 422)
        route = indexed[bindings.model_route_version]["content"]
        snapshots = [FrozenModel.model_validate(v) for v in route.get("models", [])]
        if not snapshots:
            raise ServiceError("DEPENDENCY_INVALID", "模型路由缺少具体候选模型", 422)
        if not capabilities <= set(route.get("required_capabilities", [])):
            raise ServiceError(
                "CAPABILITY_MISMATCH", "模型路由未声明智能体所需的结构化输出或工具能力", 422
            )
        embedding_snapshots: list[FrozenModel] = []
        if bindings.embedding_route_version:
            embedding_route = indexed[bindings.embedding_route_version]["content"]
            if "embedding" not in embedding_route.get("required_capabilities", []):
                raise ServiceError("CAPABILITY_MISMATCH", "语义检索路由须声明向量能力", 422)
            embedding_snapshots = [
                FrozenModel.model_validate(v) for v in embedding_route.get("models", [])
            ]
            if len(embedding_snapshots) != 1:
                raise ServiceError(
                    "CAPABILITY_MISMATCH", "向量路由须固定一个模型，避免混用向量空间", 422
                )
        for model_snapshot in [*snapshots, *embedding_snapshots]:
            required_capabilities = (
                {"embedding"} if model_snapshot in embedding_snapshots else capabilities
            )
            if (
                model_snapshot.scope.channel_id != scope.channel_id
                or model_snapshot.scope.environment != scope.environment
            ):
                raise ServiceError("DEPENDENCY_INVALID", "模型路由不属于当前渠道或环境", 422)
            model = await required(uow.connection, scope, "models", model_snapshot.model_id)
            connection = await required(
                uow.connection, scope, "model_connections", model_snapshot.connection_id
            )
            if (
                not {model_snapshot.model_version_id, model_snapshot.connection_version_id}
                <= indexed.keys()
            ):
                raise ServiceError("DEPENDENCY_INVALID", "模型路由未完整声明模型与连接依赖", 422)
            if (
                model["status"] != "ACTIVE"
                or connection["status"] != "ACTIVE"
                or not PROTOCOLS[model_snapshot.protocol].enabled
            ):
                raise ServiceError("DEPENDENCY_INVALID", "模型、连接或协议已停用", 422)
            if (
                configuration_digest(model, connection) != model_snapshot.config_digest
                or connection["credential_ref"] != model_snapshot.provider_credential_id
            ):
                raise ServiceError(
                    "DEPENDENCY_INVALID", "模型或凭据配置已变化，请重新冻结路由", 422
                )
            credential = await required(
                uow.connection, scope, "credentials", connection["credential_ref"]
            )
            if credential["state"] != "ACTIVE":
                raise ServiceError("DEPENDENCY_INVALID", "模型连接凭据已撤销", 422)
            if (
                model["context_limit"] is None
                or definition.context.context_limit > model["context_limit"]
            ):
                raise ServiceError("CAPABILITY_MISMATCH", "上下文上限超过模型容量", 422)
            try:
                if purpose == "production":
                    require_capabilities(
                        model["capabilities"],
                        model_snapshot.config_digest,
                        sorted(required_capabilities),
                    )
                elif any(
                    model["capabilities"].get(c, {}).get("state") == "UNSUPPORTED"
                    for c in required_capabilities
                ):
                    raise ServiceError("CAPABILITY_MISMATCH", "模型已明确不支持所需能力", 422)
            except (ServiceError, KeyError) as exc:
                raise ServiceError(
                    "CAPABILITY_MISMATCH", "模型所需能力未通过当前配置的真实验证", 422
                ) from exc
        policies: dict[str, Any] = {}
        if definition.context.memory_policy:
            channel = await MemoryKernel.policy(uow, context)
            validate_policy(definition.context.memory_policy, channel)
            current = await repository("memory_policies", scope).find(uow.connection, agent_id=None)
            policies["memory"] = {
                "channel_revision": current[0]["revision"] if current else 0,
                "channel": channel.model_dump(mode="json"),
                "agent": definition.context.memory_policy.model_dump(mode="json"),
            }
        return rows, policies, snapshots

    async def check_mcp(self, uow: UnitOfWork, context: AuthContext, tool: ToolDefinition) -> None:
        connection = await required(
            uow.connection, context.scope, "mcp_connections", tool.binding.connection_id or ""
        )
        if (
            connection["status"] != "ENABLED"
            or connection["auth_failed"]
            or connection["health_status"] != "HEALTHY"
            or connection["tested_revision"] != connection["configuration_revision"]
        ):
            raise ServiceError("DEPENDENCY_INVALID", "MCP 连接未通过当前配置验证或已停用", 422)
        imported = await required(
            uow.connection, context.scope, "mcp_imports", tool.binding.adapter_key
        )
        original = await required(
            uow.connection, context.scope, "mcp_discoveries", imported["discovery_id"]
        )
        discoveries = await repository("mcp_discoveries", context.scope).find(
            uow.connection, connection_id=connection["id"]
        )
        latest = (
            max(discoveries, key=lambda row: (row["created_at"], row["id"]))
            if discoveries
            else None
        )
        require_current_binding(tool.binding, connection, imported, original, latest)
        if connection["credential_ref"]:
            if (
                record_key(context.scope.channel_id, "credentials", connection["credential_ref"])
                not in uow.keys
            ):
                raise ServiceError("REVISION_CONFLICT", "MCP 凭据配置已变化，请重新检查", 409)
            credential = await required(
                uow.connection, context.scope, "credentials", connection["credential_ref"]
            )
            if (
                credential["state"] != "ACTIVE"
                or credential["revision"] != connection["credential_revision"]
            ):
                raise ServiceError(
                    "DEPENDENCY_INVALID", "MCP 凭据已撤销或变更，请重新验证连接", 422
                )


def dependency_manifest(rows: list[dict[str, Any]], policies: dict[str, Any]) -> dict[str, Any]:
    return {
        "versions": [
            {
                "version_id": row["id"],
                "resource_type": row["resource_type"],
                "content_digest": row["content_digest"],
                "draft_revision": row["revision"] if row["state"] == "DRAFT" else None,
                "dependencies": sorted(row["dependencies"]),
            }
            for row in rows
        ],
        "policies": policies,
    }
