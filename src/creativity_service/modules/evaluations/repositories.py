"""评测记录按完整受信范围读取，写入共享渠道评测锁与删除图锁。"""

from typing import Any

from sqlalchemy.ext.asyncio import AsyncConnection

from creativity_service.core.context import AuthContext, Scope
from creativity_service.core.database import Repository
from creativity_service.core.deletion import ContentRef, content_key
from creativity_service.core.locking import ResourceKey, record_key
from creativity_service.core.primitives import ServiceError, digest
from creativity_service.modules.evaluations.tables import metadata
from creativity_service.modules.iam.repositories import policy_key


def repository(name: str, scope: Scope, *, include_deleted: bool = False) -> Repository:
    return Repository(metadata.tables[name], scope)


async def required(
    connection: AsyncConnection, scope: Scope, name: str, identifier: str
) -> dict[str, Any]:
    row = await repository(name, scope).get(connection, identifier)
    if row is None:
        raise ServiceError("NOT_FOUND", "评测资源不存在", 404)
    return row


def evaluation_key(scope: Scope) -> ResourceKey:
    return ResourceKey(scope.channel_id, "evaluations", ("channel",))


def keys(context: AuthContext, records: list[tuple[str, str]] | None = None) -> list[ResourceKey]:
    return [
        content_key(context.scope),
        evaluation_key(context.scope),
        policy_key(context.scope.channel_id),
        policy_key("system"),
        *[record_key(context.scope.channel_id, t, i) for t, i in records or []],
    ]


def link_id(source: ContentRef, derived: ContentRef) -> str:
    return digest(
        [
            "evaluation",
            source.resource_type,
            source.resource_id,
            derived.resource_type,
            derived.resource_id,
        ]
    )


def link_keys(
    context: AuthContext, links: list[tuple[ContentRef, ContentRef]]
) -> list[ResourceKey]:
    return [record_key(context.scope.channel_id, "source_links", link_id(s, d)) for s, d in links]
