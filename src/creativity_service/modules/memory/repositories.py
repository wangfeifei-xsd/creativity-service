"""主体锁覆盖容量、属性冲突、开关和清空；渠道策略使用独立锁。"""

from typing import Any

from sqlalchemy import insert, select, update
from sqlalchemy.ext.asyncio import AsyncConnection

from creativity_service.core.context import Scope
from creativity_service.core.database import UnitOfWork, scope_values, validate_row
from creativity_service.core.database.soft_delete import active_rows
from creativity_service.core.deletion import content_key
from creativity_service.core.locking import ResourceKey
from creativity_service.core.primitives import ServiceError, digest, utcnow
from creativity_service.modules.memory.tables import metadata


def subject_key(scope: Scope) -> ResourceKey:
    return ResourceKey(scope.channel_id, "memory-subject", (digest(scope.model_dump()),))


def policy_key(scope: Scope) -> ResourceKey:
    return ResourceKey(scope.channel_id, "memory-policy", ("channel",))


def keys(scope: Scope) -> list[ResourceKey]:
    return [content_key(scope), subject_key(scope), policy_key(scope)]


async def rows(
    connection: AsyncConnection,
    name: str,
    scope: Scope,
    *,
    include_deleted: bool = False,
    **filters: Any,
) -> list[dict[str, Any]]:
    table = metadata.tables[name]
    if set(filters) & set(Scope.model_fields):
        raise ServiceError("CONTEXT_OVERRIDE", "不能通过筛选覆盖记忆范围", 422)
    statement = active_rows(
        select(table).where(
            *(table.c[k] == v for k, v in scope_values(table, scope).items()),
            *(table.c[k] == v for k, v in filters.items()),
        ),
        include_deleted=include_deleted,
    )
    return [dict(row) for row in (await connection.execute(statement)).mappings()]


async def one(
    connection: AsyncConnection,
    name: str,
    scope: Scope,
    *,
    include_deleted: bool = False,
    **filters: Any,
) -> dict[str, Any] | None:
    found = await rows(connection, name, scope, include_deleted=include_deleted, **filters)
    if len(found) > 1:
        raise ServiceError("STORAGE_INVARIANT_BROKEN", "记忆记录重复，请核查", 503)
    return found[0] if found else None


async def required(
    connection: AsyncConnection,
    name: str,
    scope: Scope,
    *,
    include_deleted: bool = False,
    **filters: Any,
) -> dict[str, Any]:
    row = await one(connection, name, scope, include_deleted=include_deleted, **filters)
    if row is None:
        raise ServiceError("NOT_FOUND", "记忆记录不存在", 404)
    return row


async def save(
    uow: UnitOfWork,
    name: str,
    record_id: str,
    values: dict[str, Any],
    *,
    include_deleted: bool = False,
) -> dict[str, Any]:
    if not isinstance(uow.scope, Scope):
        raise ServiceError("SCOPE_MISMATCH", "记忆必须使用业务范围", 403)
    scope, table = uow.scope, metadata.tables[name]
    uow.require_lock(policy_key(scope) if name == "memory_policies" else subject_key(scope))
    if set(values) & {"id", "revision", "created_at", "updated_at", *Scope.model_fields}:
        raise ServiceError("CONTEXT_OVERRIDE", "不能覆盖记忆归属或服务元数据", 422)
    current = await one(uow.connection, name, scope, id=record_id, include_deleted=True)
    if current and current["is_deleted"] and not include_deleted:
        raise ServiceError("NOT_FOUND", "记录已删除", 404)
    row = {
        "is_deleted": False,
        **{c.name: None for c in table.c if c.name != "is_deleted"},
        **(current or {}),
        **values,
        **scope_values(table, scope),
        "id": record_id,
        "created_at": current["created_at"] if current else utcnow(),
        "updated_at": utcnow(),
        "revision": current["revision"] + 1 if current else 1,
    }
    validate_row(table, row)
    if current:
        await uow.connection.execute(
            update(table)
            .where(
                *(table.c[k] == v for k, v in scope_values(table, scope).items()),
                table.c.id == record_id,
            )
            .values(**row)
        )
    else:
        await uow.connection.execute(insert(table).values(**row))
    return row
