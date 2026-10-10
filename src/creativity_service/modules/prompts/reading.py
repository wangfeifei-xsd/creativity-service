"""提示词查询的批量关联读取与纯响应装配。"""

from collections import defaultdict
from dataclasses import dataclass
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine

from creativity_service.core.context import AuthContext
from creativity_service.core.contracts import VisibleAction
from creativity_service.core.contracts.display import display_status
from creativity_service.core.database.soft_delete import active_rows
from creativity_service.modules.prompts.repositories import repository
from creativity_service.modules.prompts.schemas import (
    PromptReference,
    PromptReleaseView,
    PromptView,
)

ENVIRONMENTS = {"dev": "开发", "test": "测试", "fat": "验收", "prod": "生产"}


async def read_references(
    connection: AsyncConnection, context: AuthContext, versions: list[dict[str, Any]]
) -> dict[str, list[PromptReference]]:
    indexed = {row["id"]: row for row in versions}
    refs = await repository("resource_references", context.scope).find_many(
        connection, "target_version_id", indexed, target_resource_type="prompt"
    )
    sources = await repository("resource_versions", context.scope).get_many(
        connection, [ref["source_version_id"] for ref in refs]
    )
    result: dict[str, list[PromptReference]] = defaultdict(list)
    for ref in refs:
        source = sources.get(ref["source_version_id"])
        target = indexed[ref["target_version_id"]]
        if source:
            result[target["resource_id"]].append(
                PromptReference(
                    source_resource_id=source["resource_id"],
                    source_version_id=source["id"],
                    source_name=source["content"].get("name"),
                    version_label=source["version_label"],
                    status=display_status(source["state"]),
                    target_version_id=target["id"],
                    target_version_label=target["version_label"],
                    resource_type_label="智能体"
                    if source["resource_type"] == "agent"
                    else "配置资源",
                )
            )
    return dict(result)


def release_views(
    mappings: list[dict[str, Any]], versions: dict[str, dict[str, Any]]
) -> list[PromptReleaseView]:
    return [
        PromptReleaseView(
            environment=row["environment"],
            environment_label=ENVIRONMENTS[row["environment"]],
            version_id=row["version_id"],
            version_label=versions[row["version_id"]]["version_label"],
            revision=row["revision"],
            published_at=row["updated_at"],
        )
        for row in mappings
        if row["version_id"] in versions
    ]


@dataclass
class PromptReadData:
    versions: dict[str, list[dict[str, Any]]]
    references: dict[str, list[PromptReference]]
    releases: dict[str, list[PromptReleaseView]]
    last_tests: dict[str, Any]

    @classmethod
    async def load(
        cls, engine: AsyncEngine, context: AuthContext, identifiers: list[str]
    ) -> "PromptReadData":
        versions: dict[str, list[dict[str, Any]]] = defaultdict(list)
        releases: dict[str, list[PromptReleaseView]] = defaultdict(list)
        dates = {}
        async with engine.connect() as connection:
            rows = await repository("resource_versions", context.scope).find_many(
                connection, "resource_id", identifiers, resource_type="prompt"
            )
            rows = [row for row in rows if row["id"] == row["resource_id"]]
            indexed = {row["id"]: row for row in rows}
            for row in rows:
                versions[row["resource_id"]].append(row)
            references = await read_references(connection, context, rows)
            mappings = await repository("release_mappings", context.scope).find_many(
                connection, "resource_id", identifiers, resource_type="prompt"
            )
            for row in mappings:
                releases[row["resource_id"]].extend(release_views([row], indexed))
            repo = repository("prompt_tests", context.scope)
            table = repo.table
            version_ids = list(indexed)
            for start in range(0, len(version_ids), 500):
                result = await connection.execute(
                    active_rows(
                        select(table.c.version_id, func.max(table.c.created_at).label("at"))
                        .where(
                            repo.predicate(),
                            table.c.version_id.in_(version_ids[start : start + 500]),
                        )
                        .group_by(table.c.version_id)
                    )
                )
                dates.update({row["version_id"]: row["at"] for row in result.mappings()})
        return cls(dict(versions), references, dict(releases), dates)

    def view(self, row: dict[str, Any], actions: list[VisibleAction]) -> PromptView:
        versions = sorted(
            self.versions.get(row["id"], []), key=lambda v: v["created_at"], reverse=True
        )
        dates = [self.last_tests[v["id"]] for v in versions if v["id"] in self.last_tests]
        return PromptView(
            prompt_id=row["id"],
            prompt_code=row["prompt_code"],
            name=row["name"],
            purpose=row["purpose"],
            revision=row["revision"],
            version_label=versions[0]["version_label"] if versions else None,
            status=display_status(versions[0]["state"] if versions else "DRAFT"),
            releases=self.releases.get(row["id"], []),
            last_test_at=max(dates) if dates else None,
            agent_count=len(
                {
                    ref.source_resource_id
                    for ref in self.references.get(row["id"], [])
                    if ref.resource_type_label == "智能体"
                }
            ),
            actions=actions,
        )
