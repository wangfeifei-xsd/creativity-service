"""运行记录及所有派生记录共用运行锁，首次幂等和会话占用另有稳定业务锁。"""

from typing import Any

from sqlalchemy import insert, select, update
from sqlalchemy.ext.asyncio import AsyncConnection

from creativity_service.core.context import Scope
from creativity_service.core.database import UnitOfWork, scope_values, validate_row
from creativity_service.core.locking import ResourceKey
from creativity_service.core.primitives import ServiceError, utcnow
from creativity_service.modules.runs.tables import metadata


def run_key(channel_id: str, run_id: str) -> ResourceKey:
    return ResourceKey(channel_id, "run", (run_id,))


def idempotency_key(scope: Scope, scope_digest: str, key: str) -> ResourceKey:
    return ResourceKey(scope.channel_id, "run-idempotency", (scope_digest, key))


def conversation_key(scope: Scope, conversation_id: str) -> ResourceKey:
    return ResourceKey(
        scope.channel_id, "conversation-execution", (scope.environment, conversation_id)
    )


async def rows(
    connection: AsyncConnection, name: str, channel_id: str, **filters: Any
) -> list[dict[str, Any]]:
    if not channel_id or channel_id == "system":
        raise ServiceError("SCOPE_MISMATCH", "运行必须属于业务渠道", 403)
    table = metadata.tables[name]
    statement = select(table).where(
        table.c.channel_id == channel_id, *(table.c[k] == v for k, v in filters.items())
    )
    return [dict(r) for r in (await connection.execute(statement)).mappings()]


async def one(
    connection: AsyncConnection, name: str, channel_id: str, **filters: Any
) -> dict[str, Any] | None:
    found = await rows(connection, name, channel_id, **filters)
    if len(found) > 1:
        raise ServiceError("STORAGE_INVARIANT_BROKEN", "运行记录重复，请核查", 503)
    return found[0] if found else None


async def required(
    connection: AsyncConnection, name: str, channel_id: str, **filters: Any
) -> dict[str, Any]:
    row = await one(connection, name, channel_id, **filters)
    if row is None:
        raise ServiceError("NOT_FOUND", "运行记录不存在", 404)
    return row


def verify_scope(row: dict[str, Any], scope: Scope) -> None:
    if any(row[k] != v for k, v in scope.model_dump().items() if k in row):
        raise ServiceError("NOT_FOUND", "运行记录不存在", 404)


async def save(
    uow: UnitOfWork, name: str, record_id: str, values: dict[str, Any]
) -> dict[str, Any]:
    if not isinstance(uow.scope, Scope):
        raise ServiceError("SCOPE_MISMATCH", "运行不能使用控制面范围", 403)
    table, scope = metadata.tables[name], uow.scope
    current = await one(uow.connection, name, scope.channel_id, id=record_id)
    if current:
        verify_scope(current, scope)
    run_id = record_id if name == "runs" else values.get("run_id", (current or {}).get("run_id"))
    if not isinstance(run_id, str):
        raise ValueError("派生记录必须声明所属运行")
    uow.require_lock(run_key(scope.channel_id, run_id))
    if {
        "id",
        "channel_id",
        "environment",
        "data_scope_id",
        "subject_type",
        "subject_id",
        "created_at",
        "updated_at",
        "revision",
    } & values.keys():
        raise ServiceError("CONTEXT_OVERRIDE", "不能覆盖运行归属", 422)
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
    if name == "run_idempotency":
        uow.require_lock(idempotency_key(scope, row["scope_digest"], row["key"]))
    if name == "run_occupancies":
        uow.require_lock(conversation_key(scope, row["conversation_id"]))
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
