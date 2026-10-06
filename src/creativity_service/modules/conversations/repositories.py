"""会话及派生记录统一在会话锁下读写，所有查询显式限定身份范围。"""

from typing import Any

from sqlalchemy import insert, select, update
from sqlalchemy.ext.asyncio import AsyncConnection

from creativity_service.core.context import Scope
from creativity_service.core.database import UnitOfWork, scope_values, validate_row
from creativity_service.core.primitives import ServiceError, utcnow
from creativity_service.modules.conversations.tables import metadata
from creativity_service.modules.runs.repositories import conversation_key, verify_scope


async def rows(
    connection: AsyncConnection, name: str, scope: Scope, **filters: Any
) -> list[dict[str, Any]]:
    table = metadata.tables[name]
    query = select(table).where(
        *(table.c[k] == v for k, v in {**scope_values(table, scope), **filters}.items())
    )
    return [dict(row) for row in (await connection.execute(query)).mappings()]


async def one(
    connection: AsyncConnection, name: str, scope: Scope, **filters: Any
) -> dict[str, Any] | None:
    found = await rows(connection, name, scope, **filters)
    if len(found) > 1:
        raise ServiceError("STORAGE_INVARIANT_BROKEN", "会话记录重复，请核查", 503)
    return found[0] if found else None


async def required(
    connection: AsyncConnection, name: str, scope: Scope, **filters: Any
) -> dict[str, Any]:
    row = await one(connection, name, scope, **filters)
    if row is None:
        raise ServiceError("NOT_FOUND", "会话记录不存在", 404)
    return row


async def save(
    uow: UnitOfWork, name: str, record_id: str, values: dict[str, Any]
) -> dict[str, Any]:
    if not isinstance(uow.scope, Scope):
        raise ServiceError("SCOPE_MISMATCH", "会话必须使用业务身份范围", 403)
    scope, table = uow.scope, metadata.tables[name]
    current = await one(uow.connection, name, scope, id=record_id)
    conversation_id = (
        record_id
        if name == "conversations"
        else values.get("conversation_id", (current or {}).get("conversation_id"))
    )
    if not conversation_id:
        raise ValueError("会话派生记录必须声明会话")
    uow.require_lock(conversation_key(scope, conversation_id))
    protected = {
        "id",
        "channel_id",
        "environment",
        "subject_type",
        "subject_id",
        "revision",
        "created_at",
        "updated_at",
    }
    if protected & values.keys():
        raise ServiceError("CONTEXT_OVERRIDE", "不能覆盖会话身份或元数据", 422)
    if current:
        verify_scope(current, scope)
    row = {
        **{c.name: None for c in table.c},
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
            .where(table.c.channel_id == scope.channel_id, table.c.id == record_id)
            .values(**row)
        )
    else:
        await uow.connection.execute(insert(table).values(**row))
    return row
