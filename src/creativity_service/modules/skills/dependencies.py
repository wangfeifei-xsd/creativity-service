"""按目标渠道的显式绑定解析依赖；可移植名称只标识契约，不授予权限。"""

from collections.abc import Sequence
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncConnection

from creativity_service.core.context import AuthContext
from creativity_service.core.database import Repository
from creativity_service.core.primitives import ServiceError
from creativity_service.modules.models.policy import configuration_digest
from creativity_service.modules.models.tables import metadata as model_metadata
from creativity_service.modules.skills.repositories import repository
from creativity_service.modules.skills.schemas import (
    SkillDefinition,
    SkillDependency,
    SkillIssue,
    SkillSettings,
)
from creativity_service.modules.tools.schemas import ToolDefinition
from creativity_service.modules.tools.services import ToolService
from creativity_service.modules.tools.tables import metadata as tool_metadata


async def local_bindings_many(
    connection: AsyncConnection, context: AuthContext, settings: Sequence[SkillSettings]
) -> list[dict[str, str]]:
    identifiers = [
        identifier
        for value in settings
        if isinstance(value, SkillDefinition) and "tool_bindings" not in value.model_fields_set
        for identifier in value.required_tool_versions
    ]
    versions = await repository("resource_versions", context.scope).get_many(
        connection, identifiers
    )
    tools = await Repository(tool_metadata.tables["tools"], context.scope).get_many(
        connection, [v["resource_id"] for v in versions.values() if v["resource_type"] == "tool"]
    )
    result = []
    for value in settings:
        bindings = dict(value.tool_bindings)
        if isinstance(value, SkillDefinition) and "tool_bindings" not in value.model_fields_set:
            # 历史包只恢复当时的固定引用，不按名称搜索当前版本。
            for identifier in value.required_tool_versions:
                version = versions.get(identifier)
                tool = (
                    tools.get(version["resource_id"])
                    if version and version["resource_type"] == "tool"
                    else None
                )
                if tool:
                    bindings[tool["tool_code"]] = identifier
        result.append(bindings)
    return result


async def local_bindings(
    connection: AsyncConnection, context: AuthContext, settings: SkillSettings
) -> dict[str, str]:
    return (await local_bindings_many(connection, context, [settings]))[0]


async def resolve_dependencies(
    connection: AsyncConnection,
    context: AuthContext,
    settings: SkillSettings,
    tools: ToolService,
    *,
    loaded: dict[str, Any] | None = None,
) -> tuple[list[str], list[SkillDependency], list[SkillIssue]]:
    ids: list[str] = []
    views: list[SkillDependency] = []
    issues: list[SkillIssue] = []
    seen = set()
    if loaded is None:
        bindings = await local_bindings(connection, context, settings)
    else:
        bindings = dict(settings.tool_bindings)
        if (
            isinstance(settings, SkillDefinition)
            and "tool_bindings" not in settings.model_fields_set
        ):
            for identifier in settings.required_tool_versions:
                version = loaded["versions"].get(identifier)
                tool = loaded["tools"].get(version["resource_id"]) if version else None
                if tool:
                    bindings[tool["tool_code"]] = identifier
    for name in bindings.keys() - {r.tool_code for r in settings.tool_requirements}:
        issues.append(
            SkillIssue(code="SKILL_DEPENDENCY_MISSING", message="绑定未声明的工具依赖", path=name)
        )
    version_rows = (
        loaded["versions"]
        if loaded is not None
        else await repository("resource_versions", context.scope).get_many(
            connection, bindings.values()
        )
    )
    tool_rows = (
        loaded["tools"]
        if loaded is not None
        else await Repository(tool_metadata.tables["tools"], context.scope).get_many(
            connection,
            [v["resource_id"] for v in version_rows.values() if v["resource_type"] == "tool"],
        )
    )
    for requirement in settings.tool_requirements:
        reason = None
        selected_id = bindings.get(requirement.tool_code)
        version = version_rows.get(selected_id) if selected_id else None
        if version and version["resource_type"] != "tool":
            version = None
        tool = tool_rows.get(version["resource_id"]) if version else None
        if not selected_id:
            reason = "请显式绑定目标渠道的已授权工具版本"
        elif not tool or not version or version["state"] != "PUBLISHED":
            reason = "目标渠道缺少绑定的已冻结工具版本"
        elif tool["status"] != "ACTIVE":
            reason = "依赖工具已停用"
        elif requirement.source_type and tool["source_type"] != requirement.source_type:
            reason = "工具来源不符合依赖要求"
        else:
            definition = ToolDefinition.model_validate(version["content"])
            if any(
                expected is not None and expected != actual
                for expected, actual in (
                    (requirement.input_schema, definition.input_schema),
                    (requirement.output_schema, definition.output_schema),
                )
            ):
                # 保守要求契约一致，复杂 JSON Schema 不做不可靠的自动兼容推断。
                reason = "工具输入或输出 schema 与依赖契约不兼容"
            elif context.scope.environment not in definition.environments:
                reason = "工具未授权当前环境"
            else:
                try:
                    tools.registry.validate(
                        context.scope, definition, tool["source_type"], executable=True
                    )
                except ServiceError as exc:
                    reason = exc.message
        if requirement.tool_code in seen:
            reason = "同一工具不能声明多个依赖版本"
        seen.add(requirement.tool_code)
        if version:
            ids.append(version["id"])
        views.append(
            SkillDependency(
                name=tool["name"] if tool else None,
                version_label=version["version_label"] if version else requirement.version_label,
                version_id=version["id"] if version else None,
                available=reason is None,
                reason=reason,
            )
        )
        if reason:
            issues.append(
                SkillIssue(
                    code="SKILL_DEPENDENCY_MISSING", message=reason, path=requirement.tool_code
                )
            )
    if loaded is not None:
        known_agents = loaded["agents"]
    else:
        table = repository("resource_versions", context.scope)
        known_agents = (
            set(
                await connection.scalars(
                    select(table.table.c.resource_id)
                    .where(
                        table.predicate(),
                        table.table.c.resource_type == "agent",
                        table.table.c.resource_id.in_(settings.allowed_agents),
                    )
                    .distinct()
                )
            )
            if settings.allowed_agents
            else set()
        )
    for agent_id in settings.allowed_agents:
        if agent_id not in known_agents:
            issues.append(
                SkillIssue(code="SKILL_DEPENDENCY_MISSING", message="授权智能体须属于当前渠道")
            )
    variable_names = [v.name for v in settings.input_variables]
    if len(variable_names) != len(set(variable_names)):
        issues.append(SkillIssue(code="SKILL_VARIABLE_INVALID", message="输入变量名称不能重复"))
    capabilities = set(settings.required_model_capabilities)
    if capabilities:
        models = (
            loaded["models"]
            if loaded is not None
            else await Repository(model_metadata.tables["models"], context.scope).find(
                connection, status="ACTIVE"
            )
        )
        connections = (
            loaded["connections"]
            if loaded is not None
            else await Repository(
                model_metadata.tables["model_connections"], context.scope
            ).get_many(connection, [model["connection_id"] for model in models])
        )
        supported = False
        for model in models:
            link = connections.get(model["connection_id"])
            if (
                link
                and link["status"] == "ACTIVE"
                and all(
                    model["capabilities"].get(capability, {}).get("state") == "SUPPORTED"
                    and model["capabilities"][capability].get("evidence") == "live"
                    and model["capabilities"][capability].get("config_digest")
                    == configuration_digest(model, link)
                    for capability in capabilities
                )
            ):
                supported = True
        if not supported:
            issues.append(
                SkillIssue(
                    code="SKILL_DEPENDENCY_MISSING",
                    message="当前渠道没有已验证且满足所需能力的模型",
                )
            )
    return sorted(set(ids)), views, issues
