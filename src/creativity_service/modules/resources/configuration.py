"""每个资源只保存一份可编辑配置，旧执行证据由运行快照持有。"""

from typing import Any

from sqlalchemy import and_, exists, literal, or_, select, true, union_all
from sqlalchemy.ext.asyncio import AsyncConnection

from creativity_service.core.context import Scope
from creativity_service.core.database import Repository, UnitOfWork
from creativity_service.core.database.soft_delete import active_rows
from creativity_service.core.database.tables import metadata
from creativity_service.core.database.types import json_array_rows
from creativity_service.core.deletion import ContentRef, DeletionGuard, content_key
from creativity_service.core.primitives import ServiceError, digest

KINDS = frozenset({"prompt", "model_route", "tool", "skill"})
TABLES = {
    "prompt": "prompts",
    "model_route": "model_routes",
    "tool": "tools",
    "skill": "skills",
    "agent": "agents",
}
LABELS = {
    "prompt": "提示词",
    "model_route": "模型路由",
    "tool": "工具",
    "skill": "技能",
    "agent": "智能体",
}


def configuration_values(
    kind: str,
    identifier: str,
    content: dict[str, Any],
    dependencies: list[str],
    output_schema: dict[str, Any],
    principal: str,
) -> dict[str, Any]:
    return {
        "resource_type": kind,
        "resource_id": identifier,
        "version_label": "当前配置",
        "state": "DRAFT",
        "content": content,
        "content_digest": digest({"content": content, "output_schema": output_schema}),
        "dependencies": sorted(set(dependencies)),
        "dependencies_digest": digest(sorted(set(dependencies))),
        "output_schema": output_schema,
        "created_by": principal,
    }


async def require_dependencies(uow: UnitOfWork, scope: Scope, identifiers: list[str]) -> None:
    """保存依赖与下架共用内容图锁，未运行的配置引用也能阻止下架。"""
    uow.require_read_lock(content_key(scope))
    repo = Repository(metadata.tables["resource_versions"], scope)
    loaded = await repo.get_many(uow.connection, identifiers)
    managed = [r for r in loaded.values() if r["resource_type"] in KINDS]
    release_repo = Repository(metadata.tables["release_mappings"], scope)
    releases = await release_repo.find_many(
        uow.connection, "resource_id", [r["resource_id"] for r in managed]
    )
    published = {r["version_id"] for r in releases}
    if set(identifiers) - loaded.keys() or any(
        r["state"] != "PUBLISHED" or r["id"] != r["resource_id"] or r["id"] not in published
        for r in managed
    ):
        raise ServiceError("DEPENDENCY_UNPUBLISHED", "只能关联当前环境已发布的资源", 422)
    await DeletionGuard(scope).check(uow, [ContentRef("version", i) for i in identifiers])


def reference_statement(scope: Scope, identifiers: list[str]) -> Any:
    """只查询本批目标的当前配置引用，历史发布内容及运行证据不算配置引用。"""
    versions = metadata.tables["resource_versions"]
    mappings = metadata.tables["release_mappings"]
    from creativity_service.modules.agents.repositories import repository

    parents = union_all(
        *[
            active_rows(
                select(table.c.id, table.c.name, table.c.status, literal(kind).label("kind")).where(
                    table.c.channel_id == scope.channel_id, table.c.is_deleted.is_(False)
                )
            )
            for kind in TABLES
            for table in [repository(TABLES[kind], scope).table]
        ]
    ).subquery()
    targets = json_array_rows(versions.c.dependencies)
    current_agent = and_(
        versions.c.resource_type == "agent",
        or_(
            versions.c.state == "DRAFT",
            exists(
                active_rows(
                    select(mappings.c.id).where(
                        mappings.c.channel_id == scope.channel_id,
                        mappings.c.resource_type == "agent",
                        mappings.c.version_id == versions.c.id,
                    )
                )
            ),
        ),
    )
    current_resource = and_(
        versions.c.resource_type.in_(KINDS), versions.c.id == versions.c.resource_id
    )
    return active_rows(
        select(versions.c.resource_type, versions.c.resource_id, targets.c.value.label("target_id"))
        .select_from(
            versions.join(targets, true()).join(
                parents,
                and_(
                    parents.c.id == versions.c.resource_id,
                    parents.c.kind == versions.c.resource_type,
                ),
            )
        )
        .where(
            versions.c.channel_id == scope.channel_id,
            versions.c.state != "RETIRED",
            or_(current_agent, current_resource),
            targets.c.value.in_(identifiers),
        )
        .distinct()
    )


async def reference_rows(
    connection: AsyncConnection, scope: Scope, identifiers: list[str]
) -> list[dict[str, Any]]:
    if not identifiers:
        return []
    return [
        dict(row)
        for row in (await connection.execute(reference_statement(scope, identifiers))).mappings()
    ]


async def require_edit(uow: UnitOfWork, context: Any, kind: str, identifier: str) -> None:
    """共享配置的变更必须覆盖其全部已发布环境，避免从开发环境改写生产生效内容。"""
    from creativity_service.modules.agents.access import locked_policy
    from creativity_service.modules.agents.repositories import repository
    from creativity_service.modules.iam.authorization import action_allowed, effective_actions

    policy = await locked_policy(uow, context)
    permission = "model:manage" if kind == "model_route" else f"{kind}:manage"
    policy.require(context, permission, kind, identifier)
    parent = await repository(TABLES[kind], context.scope).get(uow.connection, identifier)
    if not parent or parent["status"] != "ACTIVE":
        raise ServiceError("RESOURCE_UNAVAILABLE", "资源已不可用", 409)
    mappings = metadata.tables["release_mappings"]
    environments = set(
        await uow.connection.scalars(
            active_rows(
                select(mappings.c.environment).where(
                    mappings.c.channel_id == context.scope.channel_id,
                    mappings.c.resource_type == kind,
                    mappings.c.resource_id == identifier,
                )
            )
        )
    )
    for environment in environments:
        if policy.member is not None:
            allowed = action_allowed(
                effective_actions(
                    policy.member, list(policy.grants), environment, kind, identifier
                ),
                "release:publish",
            )
        else:
            allowed = environment == context.scope.environment
            if allowed:
                policy.require(context, "release:publish", kind, identifier)
        if not allowed:
            raise ServiceError("FORBIDDEN", "修改共享配置需要其全部已发布环境的发布权限", 403)
