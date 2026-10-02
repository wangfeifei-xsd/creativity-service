"""账本写入协议：渠道账本与系统配额分别互斥，禁止不带渠道的读写。"""

from typing import Any

from sqlalchemy import insert, select, update
from sqlalchemy.ext.asyncio import AsyncConnection

from creativity_service.core.database import UnitOfWork, validate_row
from creativity_service.core.locking import ResourceKey
from creativity_service.core.primitives import ServiceError, utcnow
from creativity_service.modules.usage.tables import metadata

SYSTEM_TABLES = {"platform_limits", "platform_quota_occupancies"}


def ledger_key(channel_id: str) -> ResourceKey:
    return ResourceKey(channel_id, "usage-ledger", ("ledger-and-budgets",))


def platform_key() -> ResourceKey:
    return ResourceKey("system", "usage-platform-limits", ("admissions",))


async def rows(
    connection: AsyncConnection, name: str, channel_id: str, **filters: Any
) -> list[dict[str, Any]]:
    if not channel_id or (name in SYSTEM_TABLES) != (channel_id == "system"):
        if not (name == "usage_exports" and channel_id == "system"):
            raise ServiceError("SCOPE_MISMATCH", "用量数据范围不正确", 403)
    table = metadata.tables[name]
    result = await connection.execute(
        select(table).where(
            table.c.channel_id == channel_id, *(table.c[k] == v for k, v in filters.items())
        )
    )
    return [dict(row) for row in result.mappings()]


async def one(
    connection: AsyncConnection, name: str, channel_id: str, **filters: Any
) -> dict[str, Any] | None:
    found = await rows(connection, name, channel_id, **filters)
    if len(found) > 1:
        raise ServiceError("STORAGE_INVARIANT_BROKEN", "用量记录重复，请核查账本", 503)
    return found[0] if found else None


async def required(
    connection: AsyncConnection, name: str, channel_id: str, **filters: Any
) -> dict[str, Any]:
    result = await one(connection, name, channel_id, **filters)
    if result is None:
        raise ServiceError("NOT_FOUND", "请求资源不存在", 404)
    return result


async def save(
    uow: UnitOfWork,
    name: str,
    record_id: str,
    values: dict[str, Any],
    *,
    system: bool = False,
) -> dict[str, Any]:
    channel_id = "system" if system else uow.scope.channel_id
    if system and name not in SYSTEM_TABLES:
        raise ServiceError("CONTROL_SCOPE_INVALID", "不允许改写控制面归属", 403)
    uow.require_lock(platform_key() if name in SYSTEM_TABLES else ledger_key(channel_id))
    if {"id", "channel_id", "created_at", "updated_at", "revision"} & values.keys():
        raise ServiceError("CONTEXT_OVERRIDE", "不能覆盖用量归属或修订", 422)
    table = metadata.tables[name]
    current = await one(uow.connection, name, channel_id, id=record_id)
    now = utcnow()
    row = {
        **(current or {}),
        **values,
        "id": record_id,
        "channel_id": channel_id,
        "created_at": current["created_at"] if current else now,
        "updated_at": now,
        "revision": current["revision"] + 1 if current else 1,
    }
    validate_row(table, row)
    if current:
        await uow.connection.execute(
            update(table)
            .where(table.c.channel_id == channel_id, table.c.id == record_id)
            .values(**row)
        )
    else:
        await uow.connection.execute(insert(table).values(**row))
    return row
