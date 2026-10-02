"""模块事务内创建公共不可变版本及来源引用，不复制版本真值表。"""

from typing import Any

from creativity_service.core.context import AuthContext
from creativity_service.core.database import UnitOfWork
from creativity_service.core.deletion import ContentRef, DeletionGuard
from creativity_service.core.locking import ResourceKey, record_key
from creativity_service.core.primitives import ServiceError, digest
from creativity_service.modules.models.repositories import repository, required


def version_keys(
    channel_id: str, version_id: str, resource_type: str, resource_id: str, dependencies: list[str]
) -> list[ResourceKey]:
    return [
        record_key(channel_id, "resource_versions", version_id),
        record_key(channel_id, "source_links", digest([version_id, resource_type, resource_id])),
        *[
            record_key(channel_id, "source_links", digest([version_id, "version", dep]))
            for dep in dependencies
        ],
        *[
            record_key(channel_id, "resource_references", digest([version_id, dep]))
            for dep in dependencies
        ],
    ]


async def freeze(
    uow: UnitOfWork,
    context: AuthContext,
    version_id: str,
    resource_type: str,
    resource_id: str,
    label: str,
    content: dict[str, Any],
    dependencies: list[str],
) -> dict[str, Any]:
    scope = context.scope
    await DeletionGuard(scope).check(
        uow,
        [
            ContentRef(resource_type, resource_id),
            *[ContentRef("version", dep) for dep in dependencies],
        ],
    )
    resolved = []
    for dependency in dependencies:
        target = await required(uow.connection, scope, "resource_versions", dependency)
        if target["state"] != "PUBLISHED":
            raise ServiceError("DEPENDENCY_INVALID", "依赖版本已不可用", 409)
        resolved.append(
            {
                "version_id": dependency,
                "content_digest": target["content_digest"],
                "dependencies_digest": target["dependencies_digest"],
            }
        )
    row = await repository(scope, "resource_versions").add(
        uow,
        version_id,
        {
            "resource_type": resource_type,
            "resource_id": resource_id,
            "version_label": label,
            "state": "PUBLISHED",
            "content": content,
            "content_digest": digest({"content": content, "output_schema": {}}),
            "dependencies": dependencies,
            "dependencies_digest": digest(resolved),
            "output_schema": {},
            "created_by": context.principal_id,
        },
    )
    for source in [
        ContentRef(resource_type, resource_id),
        *[ContentRef("version", dep) for dep in dependencies],
    ]:
        await DeletionGuard(scope).link(
            uow,
            digest([version_id, source.resource_type, source.resource_id]),
            source,
            ContentRef("version", version_id),
        )
    for dep in dependencies:
        await repository(scope, "resource_references").add(
            uow,
            digest([version_id, dep]),
            {
                "source_version_id": version_id,
                "target_version_id": dep,
                "target_resource_type": (
                    await required(uow.connection, scope, "resource_versions", dep)
                )["resource_type"],
            },
        )
    return row
