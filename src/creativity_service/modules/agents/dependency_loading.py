"""按当前共享配置解析依赖，历史运行只读取已经保存的快照。"""

from typing import Any

from sqlalchemy.ext.asyncio import AsyncConnection

from creativity_service.core.context import Scope
from creativity_service.core.primitives import ServiceError
from creativity_service.modules.agents.repositories import repository
from creativity_service.modules.models.configuration import resolve_routes


async def dependency_rows(
    connection: AsyncConnection,
    scope: Scope,
    ids: list[str],
    *,
    resources: dict[str, dict[str, dict[str, Any]]] | None = None,
    limit: int = 256,
) -> list[dict[str, Any]]:
    resources = resources if resources is not None else {}
    result: dict[str, dict[str, Any]] = {}
    pending = set(ids)
    while pending:
        if len(result) + len(pending) > limit:
            raise ServiceError("DEPENDENCY_INVALID", "依赖数量超过上限", 422)
        loaded = await repository("resource_versions", scope).get_many(connection, pending)
        if pending - loaded.keys():
            raise ServiceError("DEPENDENCY_INVALID", "当前渠道缺少所需资源或版本", 422)
        loaded = await resolve_routes(connection, scope, loaded, resources=resources)
        result.update(loaded)
        pending = {dep for row in loaded.values() for dep in row["dependencies"]} - result.keys()
    visited: set[str] = set()
    visiting: set[str] = set()

    def visit(identifier: str) -> None:
        if identifier in visiting:
            raise ServiceError("DEPENDENCY_INVALID", "依赖清单存在循环", 422)
        if identifier in visited:
            return
        visiting.add(identifier)
        for dependency in result[identifier]["dependencies"]:
            visit(dependency)
        visiting.remove(identifier)
        visited.add(identifier)

    for identifier in ids:
        visit(identifier)
    return sorted(result.values(), key=lambda row: row["id"])
