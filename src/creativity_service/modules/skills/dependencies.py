"""按目标渠道重新解析工具名称与具体版本，并验证当前可执行性。"""

from typing import Any

from sqlalchemy.ext.asyncio import AsyncConnection

from creativity_service.core.context import AuthContext
from creativity_service.core.database import Repository
from creativity_service.core.primitives import ServiceError
from creativity_service.modules.models.policy import configuration_digest
from creativity_service.modules.models.tables import metadata as model_metadata
from creativity_service.modules.skills.repositories import repository
from creativity_service.modules.skills.schemas import SkillDependency, SkillIssue, SkillSettings
from creativity_service.modules.tools.schemas import ToolDefinition
from creativity_service.modules.tools.services import ToolService
from creativity_service.modules.tools.tables import metadata as tool_metadata


async def resolve_dependencies(
    connection: AsyncConnection, context: AuthContext, settings: SkillSettings, tools: ToolService
) -> tuple[list[str], list[SkillDependency], list[SkillIssue]]:
    ids: list[str] = []
    views: list[SkillDependency] = []
    issues: list[SkillIssue] = []
    seen = set()
    for requirement in settings.tool_requirements:
        reason = None
        rows = await Repository(tool_metadata.tables["tools"], context.scope).find(
            connection, tool_code=requirement.tool_code
        )
        tool: dict[str, Any] | None = rows[0] if len(rows) == 1 else None
        versions = (
            await repository("resource_versions", context.scope).find(
                connection,
                resource_type="tool",
                resource_id=tool["id"],
                version_label=requirement.version_label,
            )
            if tool
            else []
        )
        version = versions[0] if len(versions) == 1 else None
        if not tool or not version or version["state"] != "PUBLISHED":
            reason = "目标渠道缺少匹配的已冻结工具版本"
        elif tool["status"] != "ACTIVE":
            reason = "依赖工具已停用"
        else:
            definition = ToolDefinition.model_validate(version["content"])
            if (
                context.scope.environment not in definition.environments
                or context.scope.data_scope_id not in definition.allowed_data_domains
            ):
                reason = "工具未授权当前环境或业务数据域"
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
                version_label=requirement.version_label,
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
    for agent_id in settings.allowed_agents:
        versions = await repository("resource_versions", context.scope).find(
            connection, resource_type="agent", resource_id=agent_id
        )
        if not versions:
            issues.append(
                SkillIssue(code="SKILL_DEPENDENCY_MISSING", message="授权智能体须属于当前渠道")
            )
    variable_names = [v.name for v in settings.input_variables]
    if len(variable_names) != len(set(variable_names)):
        issues.append(SkillIssue(code="SKILL_VARIABLE_INVALID", message="输入变量名称不能重复"))
    capabilities = set(settings.required_model_capabilities)
    if capabilities:
        models = await Repository(model_metadata.tables["models"], context.scope).find(
            connection, status="ACTIVE"
        )
        supported = False
        for model in models:
            link = await Repository(model_metadata.tables["model_connections"], context.scope).get(
                connection, model["connection_id"]
            )
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
