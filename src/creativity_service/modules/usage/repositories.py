"""账本写入协议：渠道账本与系统配额分别互斥，禁止不带渠道的读写。"""

from typing import Any

from sqlalchemy import insert, select, update
from sqlalchemy.ext.asyncio import AsyncConnection

from creativity_service.core.database import UnitOfWork, validate_row
from creativity_service.core.database.queries import scoped_select
from creativity_service.core.database.soft_delete import active_rows
from creativity_service.core.locking import ResourceKey
from creativity_service.core.primitives import ServiceError, utcnow
from creativity_service.modules.usage.tables import metadata

SYSTEM_TABLES = {"platform_limits", "platform_quota_occupancies"}


def ledger_key(channel_id: str) -> ResourceKey:
    return ResourceKey(channel_id, "usage-ledger", ("ledger-and-budgets",))


def budget_configuration_key(channel_id: str) -> ResourceKey:
    return ResourceKey(channel_id, "usage-configuration", ("policies-and-prices",))


def platform_key() -> ResourceKey:
    return ResourceKey("system", "usage-platform-limits", ("admissions",))


async def rows(
    connection: AsyncConnection,
    name: str,
    channel_id: str,
    *,
    include_deleted: bool = False,
    **filters: Any,
) -> list[dict[str, Any]]:
    if not channel_id or (name in SYSTEM_TABLES) != (channel_id == "system"):
        if not (name == "usage_exports" and channel_id == "system"):
            raise ServiceError("SCOPE_MISMATCH", "用量数据范围不正确", 403)
    table = metadata.tables[name]
    statement, parameters = scoped_select(
        table, {"channel_id": channel_id}, filters, include_deleted=include_deleted
    )
    result = await connection.execute(statement, parameters)
    return [dict(row) for row in result.mappings()]


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
        raise ServiceError("STORAGE_INVARIANT_BROKEN", "用量记录重复，请核查账本", 503)
    return found[0] if found else None


async def required(
    connection: AsyncConnection,
    name: str,
    channel_id: str,
    *,
    include_deleted: bool = False,
    **filters: Any,
) -> dict[str, Any]:
    result = await one(connection, name, channel_id, include_deleted=include_deleted, **filters)
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
    include_deleted: bool = False,
) -> dict[str, Any]:
    channel_id = "system" if system else uow.scope.channel_id
    if system and name not in SYSTEM_TABLES:
        raise ServiceError("CONTROL_SCOPE_INVALID", "不允许改写控制面归属", 403)
    uow.require_lock(platform_key() if name in SYSTEM_TABLES else ledger_key(channel_id))
    if name in {"budget_policies", "price_versions"}:
        uow.require_lock(budget_configuration_key(channel_id))
    if {"id", "channel_id", "created_at", "updated_at", "revision"} & values.keys():
        raise ServiceError("CONTEXT_OVERRIDE", "不能覆盖用量归属或修订", 422)
    table = metadata.tables[name]
    current = await one(uow.connection, name, channel_id, id=record_id, include_deleted=True)
    if current and current["is_deleted"] and not include_deleted:
        raise ServiceError("NOT_FOUND", "记录已删除", 404)
    now = utcnow()
    row = {
        "is_deleted": False,
        **(current or {}),
        **values,
        "id": record_id,
        "channel_id": channel_id,
        "created_at": current["created_at"] if current else now,
        "updated_at": now,
        "revision": current["revision"] + 1 if current else 1,
    }
    validate_row(table, row)
    # 同一事务的占用、策略发生写入后，后续校验必须看到本次写入。
    prefix = "usage-read:" + name
    for key in list(uow.read_cache):
        if key == prefix or key.startswith(prefix + ":"):
            del uow.read_cache[key]
    if current:
        await uow.connection.execute(
            update(table)
            .where(table.c.channel_id == channel_id, table.c.id == record_id)
            .values(**row)
        )
    else:
        await uow.connection.execute(insert(table).values(**row))
    return row


async def add_platform_occupancies(uow: UnitOfWork, records: dict[str, dict[str, Any]]) -> None:
    """平台锁内批量查重和登记占用；限额数量不会增加逐条网络往返。"""
    uow.require_lock(platform_key())
    if not records:
        return
    table = metadata.tables["platform_quota_occupancies"]
    now = utcnow()
    prepared = []
    for identifier, values in records.items():
        if {"id", "channel_id", "created_at", "updated_at", "revision"} & values.keys():
            raise ServiceError("CONTEXT_OVERRIDE", "不能覆盖平台配额归属", 422)
        if values.get("target_channel_id") != uow.scope.channel_id:
            raise ServiceError("SCOPE_MISMATCH", "占用必须属于当前业务渠道", 403)
        row = {
            "is_deleted": False,
            **values,
            "id": identifier,
            "channel_id": "system",
            "created_at": now,
            "updated_at": now,
            "revision": 1,
        }
        validate_row(table, row)
        prepared.append(row)
    for start in range(0, len(prepared), 100):
        batch = prepared[start : start + 100]
        if (
            await uow.connection.scalar(
                active_rows(
                    select(table.c.id)
                    .where(
                        table.c.channel_id == "system", table.c.id.in_([row["id"] for row in batch])
                    )
                    .limit(1)
                )
            )
            is not None
        ):
            raise ServiceError("DUPLICATE_ID", "平台占用记录标识已存在", 409)
        await uow.connection.execute(insert(table), batch)
