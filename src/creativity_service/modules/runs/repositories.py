"""运行记录及所有派生记录共用运行锁，首次幂等和会话占用另有稳定业务锁。"""

from typing import Any

from sqlalchemy import insert, update
from sqlalchemy.ext.asyncio import AsyncConnection

from creativity_service.core.context import Scope
from creativity_service.core.database import UnitOfWork, scope_values, validate_row
from creativity_service.core.database.inserts import InsertBatch
from creativity_service.core.database.queries import scoped_select
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
    connection: AsyncConnection,
    name: str,
    channel_id: str,
    *,
    include_deleted: bool = False,
    **filters: Any,
) -> list[dict[str, Any]]:
    if not channel_id or channel_id == "system":
        raise ServiceError("SCOPE_MISMATCH", "运行必须属于业务渠道", 403)
    table = metadata.tables[name]
    statement, parameters = scoped_select(
        table, {"channel_id": channel_id}, filters, include_deleted=include_deleted
    )
    return [dict(r) for r in (await connection.execute(statement, parameters)).mappings()]


async def one(
    connection: AsyncConnection,
    name: str,
    channel_id: str,
    *,
    include_deleted: bool = False,
    **filters: Any,
) -> dict[str, Any] | None:
    found = await rows(connection, name, channel_id, include_deleted=include_deleted, **filters)
    if len(found) > 1:
        raise ServiceError("STORAGE_INVARIANT_BROKEN", "运行记录重复，请核查", 503)
    return found[0] if found else None


async def required(
    connection: AsyncConnection,
    name: str,
    channel_id: str,
    *,
    include_deleted: bool = False,
    **filters: Any,
) -> dict[str, Any]:
    row = await one(connection, name, channel_id, include_deleted=include_deleted, **filters)
    if row is None:
        raise ServiceError("NOT_FOUND", "运行记录不存在", 404)
    return row


def verify_scope(row: dict[str, Any], scope: Scope) -> None:
    if any(row[k] != v for k, v in scope.model_dump().items() if k in row):
        raise ServiceError("NOT_FOUND", "运行记录不存在", 404)


def prepare_row(
    uow: UnitOfWork,
    name: str,
    record_id: str,
    values: dict[str, Any],
    current: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """普通写入与受理批量写入共用范围、锁、字段和完整性校验。"""
    if not isinstance(uow.scope, Scope):
        raise ServiceError("SCOPE_MISMATCH", "运行不能使用控制面范围", 403)
    table, scope = metadata.tables[name], uow.scope
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
        "subject_type",
        "subject_id",
        "created_at",
        "updated_at",
        "revision",
    } & values.keys():
        raise ServiceError("CONTEXT_OVERRIDE", "不能覆盖运行归属", 422)
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
    if name == "run_idempotency":
        uow.require_lock(idempotency_key(scope, row["scope_digest"], row["key"]))
    if name == "run_occupancies":
        uow.require_lock(conversation_key(scope, row["conversation_id"]))
    validate_row(table, row)
    return row


async def save(
    uow: UnitOfWork, name: str, record_id: str, values: dict[str, Any]
) -> dict[str, Any]:
    table, scope = metadata.tables[name], uow.scope
    current = await one(uow.connection, name, scope.channel_id, id=record_id, include_deleted=True)
    if current and current["is_deleted"]:
        raise ServiceError("NOT_FOUND", "记录已删除", 404)
    row = prepare_row(uow, name, record_id, values, current)
    if current:
        await uow.connection.execute(
            update(table)
            .where(table.c.channel_id == scope.channel_id, table.c.id == record_id)
            .values(**row)
        )
    else:
        await uow.connection.execute(insert(table).values(**row))
    return row


class NewRunRecords:
    """同一受理的新记录批量查重并写入，避免每条执行一读一写。"""

    def __init__(self, uow: UnitOfWork) -> None:
        self.uow = uow
        self.batch = InsertBatch(uow)

    def add(self, name: str, identifier: str, values: dict[str, Any]) -> dict[str, Any]:
        row = prepare_row(self.uow, name, identifier, values)
        self.batch.stage(metadata.tables[name], row)
        return row

    async def flush(self) -> None:
        await self.batch.flush()
