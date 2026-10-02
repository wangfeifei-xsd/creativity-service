"""智能体仓储按受信渠道查询；公共版本和环境映射保持唯一真值。"""

from typing import Any

from sqlalchemy.ext.asyncio import AsyncConnection

from creativity_service.core.context import Scope
from creativity_service.core.database import Repository
from creativity_service.core.database.tables import metadata as core_metadata
from creativity_service.core.primitives import ServiceError
from creativity_service.modules.agents.tables import metadata
from creativity_service.modules.channels.tables import metadata as channel_metadata
from creativity_service.modules.mcp.tables import metadata as mcp_metadata
from creativity_service.modules.memory.tables import metadata as memory_metadata
from creativity_service.modules.models.tables import metadata as model_metadata
from creativity_service.modules.prompts.tables import metadata as prompt_metadata
from creativity_service.modules.skills.tables import metadata as skill_metadata
from creativity_service.modules.tools.tables import metadata as tool_metadata

TABLES = {
    **core_metadata.tables,
    **metadata.tables,
    **model_metadata.tables,
    **tool_metadata.tables,
    **skill_metadata.tables,
    **prompt_metadata.tables,
    **memory_metadata.tables,
    **mcp_metadata.tables,
    **channel_metadata.tables,
}
RESOURCE_TABLES = {
    "agent": "agents",
    "tool": "tools",
    "prompt": "prompts",
    "skill": "skills",
    "model_route": "model_routes",
    "model": "models",
    "model_connection": "model_connections",
}


def repository(name: str, scope: Scope) -> Repository:
    return Repository(TABLES[name], scope)


async def required(
    connection: AsyncConnection, scope: Scope, name: str, identifier: str
) -> dict[str, Any]:
    row = await repository(name, scope).get(connection, identifier)
    if row is None:
        raise ServiceError("DEPENDENCY_INVALID", "当前渠道缺少所需资源或版本", 422)
    return row


async def dependency_rows(
    connection: AsyncConnection, scope: Scope, ids: list[str]
) -> list[dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    visiting: set[str] = set()

    async def visit(identifier: str) -> None:
        if identifier in visiting:
            raise ServiceError("DEPENDENCY_INVALID", "依赖清单存在循环", 422)
        if identifier in result:
            return
        if len(result) + len(visiting) >= 256:
            raise ServiceError("DEPENDENCY_INVALID", "依赖数量超过上限", 422)
        visiting.add(identifier)
        row = await required(connection, scope, "resource_versions", identifier)
        for dependency in row["dependencies"]:
            await visit(dependency)
        visiting.remove(identifier)
        result[identifier] = row

    for identifier in ids:
        await visit(identifier)
    return sorted(result.values(), key=lambda row: row["id"])
