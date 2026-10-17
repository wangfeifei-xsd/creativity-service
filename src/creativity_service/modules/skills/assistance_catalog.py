"""技能助手只读取有界的已发布资源目录，授权过滤后才提供给模型。"""

from typing import Any

from sqlalchemy import exists, select

from creativity_service.core.context import AuthContext
from creativity_service.core.database import transaction
from creativity_service.core.database.soft_delete import active_rows
from creativity_service.core.deletion import ContentRef, DeletionGuard, content_key
from creativity_service.core.primitives import ServiceError
from creativity_service.modules.agents.repositories import repository
from creativity_service.modules.iam.reading import resource_state
from creativity_service.modules.skills.services import SkillService


async def published_catalog(
    skills: SkillService, context: AuthContext, kinds: tuple[str, ...]
) -> list[dict[str, Any]]:
    policy = await skills.authorization.read_policy(context)
    versions = repository("resource_versions", context.scope)
    mappings = repository("release_mappings", context.scope)
    table, releases = versions.table, mappings.table
    statement = active_rows(
        select(table)
        .where(
            versions.predicate(),
            table.c.resource_type.in_(kinds),
            table.c.id == table.c.resource_id,
            table.c.state == "PUBLISHED",
            exists(
                active_rows(
                    select(releases.c.id).where(
                        mappings.predicate(),
                        releases.c.resource_type == table.c.resource_type,
                        releases.c.version_id == table.c.id,
                    )
                )
            ),
        )
        .order_by(table.c.id)
        .limit(129)
    )
    async with transaction(skills.engine, context.scope, [content_key(context.scope)]) as uow:
        rows = [dict(row) for row in (await uow.connection.execute(statement)).mappings()]
        if len(rows) > 128:
            raise ServiceError(
                "ASSISTANCE_CATALOG_LIMIT", "资源目录超过智能协助上限（128 个）", 422
            )
        parents = {}
        for kind in kinds:
            parents[kind] = await repository(
                "model_routes" if kind == "model_route" else "tools",
                context.scope,
            ).get_many(
                uow.connection, [row["resource_id"] for row in rows if row["resource_type"] == kind]
            )
        allowed = []
        for row in rows:
            kind = row["resource_type"]
            parent = parents[kind].get(row["resource_id"])
            if (
                parent
                and parent["status"] == "ACTIVE"
                and "run:create"
                in policy.actions(
                    kind,
                    row["resource_id"],
                    resource_state(context, kind, parent),
                )
            ):
                allowed.append({**row, "resource_name": parent["name"]})
        blocked = await DeletionGuard(context.scope).blocked_refs(
            uow,
            [
                ref
                for row in allowed
                for ref in (
                    ContentRef("version", row["id"]),
                    ContentRef(row["resource_type"], row["resource_id"]),
                )
            ],
        )
        return [
            row
            for row in allowed
            if not blocked.intersection(
                {
                    ContentRef("version", row["id"]),
                    ContentRef(row["resource_type"], row["resource_id"]),
                }
            )
        ]
