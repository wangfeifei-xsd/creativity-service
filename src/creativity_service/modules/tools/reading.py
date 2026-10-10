"""工具列表和详情共用的批量关联读取，不执行入口鉴权或响应流程。"""

from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncEngine

from creativity_service.core.context import AuthContext
from creativity_service.core.database import Repository
from creativity_service.core.database.soft_delete import active_rows
from creativity_service.core.database.tables import metadata as core_metadata
from creativity_service.modules.agents.repositories import RESOURCE_TABLES, repository
from creativity_service.modules.tools.schemas import ToolImpact, ToolReference
from creativity_service.modules.tools.tables import metadata


@dataclass
class ToolReadData:
    versions: dict[str, list[dict[str, Any]]]
    impacts: dict[str, ToolImpact]

    @classmethod
    async def load(
        cls,
        engine: AsyncEngine,
        context: AuthContext,
        tool_ids: Iterable[str],
        *,
        target_versions: list[dict[str, Any]] | None = None,
    ) -> "ToolReadData":
        identifiers = list(dict.fromkeys(tool_ids))
        versions_by_tool: dict[str, list[dict[str, Any]]] = defaultdict(list)
        refs_by_tool: dict[str, list[ToolReference]] = defaultdict(list)
        counts: dict[str, int] = {}
        async with engine.connect() as connection:
            versions = Repository(core_metadata.tables["resource_versions"], context.scope)
            targets = (
                target_versions
                if target_versions is not None
                else await versions.find_many(
                    connection, "resource_id", identifiers, resource_type="tool"
                )
            )
            target_tools = {row["id"]: row["resource_id"] for row in targets}
            for row in targets:
                versions_by_tool[row["resource_id"]].append(row)
            references = await Repository(
                core_metadata.tables["resource_references"], context.scope
            ).find_many(connection, "target_version_id", target_tools)
            sources = await versions.get_many(
                connection, [r["source_version_id"] for r in references]
            )
            resources = {}
            for kind in {r["resource_type"] for r in sources.values()} & RESOURCE_TABLES.keys():
                resources[kind] = await repository(RESOURCE_TABLES[kind], context.scope).get_many(
                    connection,
                    [r["resource_id"] for r in sources.values() if r["resource_type"] == kind],
                )
            for ref in references:
                source = sources.get(ref["source_version_id"])
                if source is None:
                    continue
                resource = resources.get(source["resource_type"], {}).get(source["resource_id"])
                refs_by_tool[target_tools[ref["target_version_id"]]].append(
                    ToolReference(
                        resource_name=resource.get("name") if resource else None,
                        resource_type=source["resource_type"],
                        version_id=source["id"],
                        version_label=source["version_label"],
                    )
                )
            calls = metadata.tables["tool_calls"]
            for start in range(0, len(identifiers), 500):
                result = await connection.execute(
                    active_rows(
                        select(calls.c.tool_id, func.count().label("count"))
                        .where(
                            calls.c.channel_id == context.scope.channel_id,
                            calls.c.environment == context.scope.environment,
                            calls.c.tool_id.in_(identifiers[start : start + 500]),
                            calls.c.state == "STARTED",
                        )
                        .group_by(calls.c.tool_id)
                    )
                )
                counts.update({r["tool_id"]: r["count"] for r in result.mappings()})
        return cls(
            dict(versions_by_tool),
            {
                identifier: ToolImpact(
                    tool_id=identifier,
                    references=refs_by_tool[identifier],
                    ongoing_calls=counts.get(identifier, 0),
                    message="停用后阻断后续调用和重试，进行中的结果交付将再次校验。",
                )
                for identifier in identifiers
            },
        )
