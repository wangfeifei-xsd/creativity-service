"""锁下解析完整依赖、当前授权和模型证据，禁止技能扩展工具白名单。"""

from typing import Any

from jsonschema import Draft202012Validator

from creativity_service.core.context import AuthContext
from creativity_service.core.database import UnitOfWork
from creativity_service.core.database.queries import latest_per_group
from creativity_service.core.deletion import ContentRef, DeletionGuard
from creativity_service.core.locking import record_key
from creativity_service.core.primitives import ServiceError, digest
from creativity_service.core.versioning import version_view
from creativity_service.modules.agents.access import locked_policy
from creativity_service.modules.agents.repositories import (
    RESOURCE_TABLES,
    TABLES,
    dependency_rows,
    repository,
)
from creativity_service.modules.agents.schemas import AgentDefinition, Purpose
from creativity_service.modules.agents.validation import compatible, schema_field
from creativity_service.modules.mcp.bindings import require_current_binding
from creativity_service.modules.memory.base import MemoryKernel
from creativity_service.modules.memory.validation import validate_policy
from creativity_service.modules.models.policy import (
    CAPABILITY_NAMES,
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
        *,
        loaded: list[dict[str, Any]] | None = None,
        resources: dict[str, dict[str, dict[str, Any]]] | None = None,
    ) -> tuple[list[dict[str, Any]], dict[str, Any], list[FrozenModel]]:
        bindings, scope = definition.bindings, context.scope
        for step in definition.steps:
            if step.kind == "model" and (
                not (step.dependency or bindings.model_route_version)
                or not (
                    step.prompt_id or bindings.prompt_version or definition.instructions.strip()
                )
            ):
                raise ServiceError(
                    "DEPENDENCY_INVALID",
                    f"步骤“{step.name}”请选择模型路由，并填写任务指令或选择提示词",
                    422,
                )
        identifiers = definition.dependency_ids()
        rows = (
            loaded
            if loaded is not None
            else await dependency_rows(uow.connection, scope, identifiers)
        )
        indexed = {r["id"]: r for r in rows}
        from creativity_service.modules.resources.configuration import require_dependencies

        await require_dependencies(uow, scope, list(dict.fromkeys([*identifiers, *indexed])))
        types = [
            *([(bindings.prompt_version, "prompt")] if bindings.prompt_version else []),
            *((identifier, "model_route") for identifier in definition.model_route_ids()),
            *((step.prompt_id, "prompt") for step in definition.steps if step.prompt_id),
            *((identifier, "tool") for identifier in bindings.tool_versions),
            *((identifier, "skill") for identifier in bindings.skill_versions),
        ]
        if bindings.embedding_route_version:
            types.append((bindings.embedding_route_version, "model_route"))
            if (
                not definition.context.memory_policy
                or not definition.context.memory_policy.read_enabled
            ):
                raise ServiceError(
                    "DEPENDENCY_INVALID",
                    "语义检索需要启用长期记忆及记忆读取；不使用时请清空语义检索模型路由",
                    422,
                )
        for identifier, kind in types:
            if indexed[identifier]["resource_type"] != kind:
                raise ServiceError("DEPENDENCY_INVALID", "依赖资源类型与选择位置不符", 422)
        capabilities = {"text"}
        if definition.workflow_type == "tool_loop":
            capabilities.add("tools")
        memory_policy = definition.context.memory_policy
        if (
            definition.context.conversation_enabled
            and memory_policy
            and memory_policy.suggest_enabled
            and memory_policy.write_mode != "DISABLED"
        ):
            if not bindings.model_route_version:
                raise ServiceError("DEPENDENCY_INVALID", "记忆建议需要配置默认模型路由", 422)
        parents = {
            kind: await repository(table, scope).get_many(
                uow.connection, [row["resource_id"] for row in rows if row["resource_type"] == kind]
            )
            for kind, table in RESOURCE_TABLES.items()
            if any(row["resource_type"] == kind for row in rows)
        }
        if resources is not None:
            resources.update(parents)
        policy = await locked_policy(uow, context)
        mcp_tools = [
            ToolDefinition.model_validate(row["content"])
            for row in rows
            if row["resource_type"] == "tool"
            and parents.get("tool", {}).get(row["resource_id"], {}).get("source_type") == "mcp"
        ]
        mcp_data = await self.mcp_data(uow, context, mcp_tools)
        await DeletionGuard(scope).check(
            uow,
            [ContentRef("version", row["id"]) for row in rows]
            + [ContentRef(row["resource_type"], row["resource_id"]) for row in rows],
        )
        for row in rows:
            kind = row["resource_type"]
            if kind not in RESOURCE_TABLES or kind == "agent":
                raise ServiceError("DEPENDENCY_INVALID", "依赖清单包含不支持的资源类型", 422)
            if row["state"] not in {"DRAFT", "PUBLISHED"} or (
                purpose == "production" and row["state"] != "PUBLISHED"
            ):
                raise ServiceError("DEPENDENCY_INVALID", "正式使用依赖须为当前环境已发布资源", 422)
            if row["content_digest"] != digest(
                {"content": row["content"], "output_schema": row["output_schema"]}
            ):
                raise ServiceError("DEPENDENCY_INVALID", "依赖内容摘要不一致", 409)
            resource = parents.get(kind, {}).get(row["resource_id"])
            if resource is None:
                raise ServiceError("DEPENDENCY_INVALID", "当前渠道缺少所需资源或版本", 422)
            row["resource_name"] = resource["name"]
            if resource.get("status", "ACTIVE") != "ACTIVE":
                raise ServiceError(
                    "DEPENDENCY_INVALID", f"{resource.get('name', '依赖资源')}已停用", 422
                )
            if kind not in {"model_connection", "model_route"}:
                policy.require(context, "run:create", kind, resource["id"])
            if row["state"] == "DRAFT":
                policy.require(context, "version:edit", kind, resource["id"])
            if kind == "tool":
                if row["id"] not in bindings.tool_versions:
                    raise ServiceError(
                        "DEPENDENCY_INVALID", "技能所需工具不在智能体工具白名单中", 422
                    )
                tool = ToolDefinition.model_validate(row["content"])
                validate_definition(tool)
                if scope.environment not in tool.environments:
                    raise ServiceError("DEPENDENCY_INVALID", "工具未授权当前环境", 422)
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
                    policy.require(context, action, "tool", resource["id"])
                self.tools.registry.validate(scope, tool, resource["source_type"], executable=True)
                if resource["source_type"] == "mcp":
                    self.check_mcp(uow, context, tool, mcp_data)
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
                capabilities.update(skill.required_model_capabilities)
                for variable in skill.input_variables:
                    source = schema_field(definition.input_schema, variable.name)
                    if variable.required and (
                        not source or not compatible(source, {"type": variable.value_type})
                    ):
                        raise ServiceError("FLOW_INVALID", "技能必填变量缺少兼容的运行输入", 422)
        await SkillVersionValidator(self.skills).validate_many(
            uow,
            context,
            [version_view(row) for row in rows if row["resource_type"] == "skill"],
            known_versions=indexed,
            resources=parents,
        )
        # 默认提示词读取运行输入；节点提示词读取映射后的步骤输入。
        prompt_uses = (
            [(bindings.prompt_version, definition.input_schema, "默认提示词")]
            if bindings.prompt_version
            else []
        )
        prompt_uses.extend(
            (step.prompt_id, step.input_schema, f"步骤“{step.name}”的提示词")
            for step in definition.steps
            if step.kind == "model" and step.prompt_id
        )
        for prompt_id, input_schema, label in prompt_uses:
            prompt = PromptContent.model_validate(indexed[prompt_id]["content"])
            for prompt_variable in prompt.variables:
                if prompt_variable.source == "input" and prompt_variable.required:
                    source = schema_field(input_schema, prompt_variable.name)
                    if source is None or not compatible(source, {"type": prompt_variable.type}):
                        raise ServiceError(
                            "FLOW_INVALID",
                            f"{label}变量“{prompt_variable.display_name}”缺少兼容输入",
                            422,
                        )
                if prompt_variable.source == "tool" and not bindings.tool_versions:
                    raise ServiceError("DEPENDENCY_INVALID", "提示词声明工具来源但未选择工具", 422)
                if prompt_variable.source == "memory" and not definition.context.memory_policy:
                    raise ServiceError(
                        "DEPENDENCY_INVALID", "提示词声明记忆来源但未配置记忆策略", 422
                    )
        snapshots: list[FrozenModel] = []
        model_uses: list[tuple[FrozenModel, set[str]]] = []
        for route_id in definition.model_route_ids():
            route = indexed[route_id]["content"]
            candidates = [FrozenModel.model_validate(v) for v in route.get("models", [])]
            if not candidates:
                raise ServiceError("DEPENDENCY_INVALID", "模型路由缺少具体候选模型", 422)
            declared = set(route.get("required_capabilities", []))
            required = set(capabilities)
            if (
                route_id == bindings.model_route_version
                and definition.context.conversation_enabled
                and memory_policy
                and memory_policy.suggest_enabled
                and memory_policy.write_mode != "DISABLED"
            ):
                required.add("structured_output")
            missing = required - declared
            if missing:
                raise ServiceError(
                    "CAPABILITY_MISMATCH",
                    f"模型路由“{indexed[route_id]['resource_name']}”未声明当前流程所需能力："
                    + "、".join(CAPABILITY_NAMES[c] for c in sorted(missing)),
                    422,
                )
            snapshots.extend(candidates)
            model_uses.extend((model, required | declared) for model in candidates)
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
        credentials = await repository("credentials", scope).get_many(
            uow.connection,
            [row["credential_ref"] for row in parents.get("model_connection", {}).values()],
        )
        # 同一模型用于生成和检索时必须分别满足两种用途，不能只检查向量能力。
        for model_snapshot, required_capabilities in [
            *model_uses,
            *((model, {"embedding"}) for model in embedding_snapshots),
        ]:
            if (
                model_snapshot.scope.channel_id != scope.channel_id
                or model_snapshot.scope.environment != scope.environment
            ):
                raise ServiceError("DEPENDENCY_INVALID", "模型路由不属于当前渠道或环境", 422)
            model = parents.get("model", {}).get(model_snapshot.model_id)
            connection = parents.get("model_connection", {}).get(model_snapshot.connection_id)
            if model is None or connection is None:
                raise ServiceError("DEPENDENCY_INVALID", "模型路由缺少所需模型或连接", 422)
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
            credential = credentials.get(connection["credential_ref"])
            if credential is None or credential["state"] != "ACTIVE":
                raise ServiceError("DEPENDENCY_INVALID", "模型连接凭据已撤销", 422)
            if (
                model["context_limit"] is not None
                and definition.context.context_limit > model["context_limit"]
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

    @staticmethod
    async def mcp_data(
        uow: UnitOfWork, context: AuthContext, tools: list[ToolDefinition]
    ) -> dict[str, dict[str, dict[str, Any]]]:
        if not tools:
            return {}
        connections = await repository("mcp_connections", context.scope).get_many(
            uow.connection,
            [tool.binding.connection_id for tool in tools if tool.binding.connection_id],
        )
        imports = await repository("mcp_imports", context.scope).get_many(
            uow.connection, [tool.binding.adapter_key for tool in tools]
        )
        originals = await repository("mcp_discoveries", context.scope).get_many(
            uow.connection, [row["discovery_id"] for row in imports.values()]
        )
        table = TABLES["mcp_discoveries"]
        latest = {
            row["connection_id"]: dict(row)
            for row in (
                await uow.connection.execute(
                    latest_per_group(
                        table,
                        "connection_id",
                        repository("mcp_discoveries", context.scope).predicate(),
                        table.c.connection_id.in_(connections),
                    )
                )
            ).mappings()
        }
        credentials = await repository("credentials", context.scope).get_many(
            uow.connection,
            [row["credential_ref"] for row in connections.values() if row["credential_ref"]],
        )
        return {
            "connections": connections,
            "imports": imports,
            "originals": originals,
            "latest": latest,
            "credentials": credentials,
        }

    @staticmethod
    def check_mcp(
        uow: UnitOfWork,
        context: AuthContext,
        tool: ToolDefinition,
        data: dict[str, dict[str, dict[str, Any]]],
    ) -> None:
        connection = data["connections"].get(tool.binding.connection_id or "")
        imported = data["imports"].get(tool.binding.adapter_key)
        if connection is None or imported is None:
            raise ServiceError("DEPENDENCY_INVALID", "当前渠道缺少所需 MCP 绑定", 422)
        if (
            connection["status"] != "ENABLED"
            or connection["auth_failed"]
            or connection["health_status"] != "HEALTHY"
            or connection["tested_revision"] != connection["configuration_revision"]
        ):
            raise ServiceError("DEPENDENCY_INVALID", "MCP 连接未通过当前配置验证或已停用", 422)
        original = data["originals"].get(imported["discovery_id"])
        if original is None:
            raise ServiceError("DEPENDENCY_INVALID", "当前渠道缺少所需 MCP 发现记录", 422)
        latest = data["latest"].get(connection["id"])
        require_current_binding(tool.binding, connection, imported, original, latest)
        if connection["credential_ref"]:
            try:
                uow.require_read_lock(
                    record_key(
                        context.scope.channel_id, "credentials", connection["credential_ref"]
                    )
                )
            except RuntimeError as exc:
                raise ServiceError(
                    "REVISION_CONFLICT", "MCP 凭据配置已变化，请重新检查", 409
                ) from exc
            credential = data["credentials"].get(connection["credential_ref"])
            if (
                credential is None
                or credential["state"] != "ACTIVE"
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
