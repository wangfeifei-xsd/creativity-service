"""生命周期写入复用渠道内容图锁，任务范围来自数据库记录。"""

from collections.abc import Iterable
from typing import Any

from sqlalchemy import insert, select, update
from sqlalchemy.ext.asyncio import AsyncConnection

from creativity_service.core.context import AuthContext, Scope
from creativity_service.core.database import Repository, UnitOfWork, scope_values, validate_row
from creativity_service.core.deletion import content_key
from creativity_service.core.primitives import ServiceError, utcnow
from creativity_service.storage import metadata


def scope_of(row: dict[str, Any]) -> Scope:
    # 渠道配置没有环境列；清理执行范围固定为开发环境，不扩大个人范围。
    return Scope.model_validate(
        {
            **{k: row.get(k) for k in Scope.model_fields},
            "environment": row.get("environment", "dev"),
        }
    )


def worker_context(scope: Scope) -> AuthContext:
    return AuthContext(
        scope=scope,
        principal_type="worker",
        principal_id="data_lifecycle",
        request_id="data_lifecycle",
    )


async def rows(
    connection: AsyncConnection, channel_id: str, name: str, **filters: Any
) -> list[dict[str, Any]]:
    table = metadata.tables[name]
    result = await connection.execute(
        select(table).where(
            table.c.channel_id == channel_id,
            *(table.c[k] == v for k, v in filters.items()),
        )
    )
    return [dict(row) for row in result.mappings()]


async def related_rows(
    connection: AsyncConnection, channel_id: str, name: str, field: str, identifiers: Iterable[str]
) -> list[dict[str, Any]]:
    """批量解析已定位任务的关联记录，继续限制在明确渠道内。"""
    table = metadata.tables[name]
    values = list(dict.fromkeys(identifiers))
    result: list[dict[str, Any]] = []
    for start in range(0, len(values), 500):
        found = await connection.execute(
            select(table).where(
                table.c.channel_id == channel_id,
                table.c[field].in_(values[start : start + 500]),
            )
        )
        result.extend(dict(row) for row in found.mappings())
    return result


async def put(
    uow: UnitOfWork, name: str, identifier: str, values: dict[str, Any]
) -> dict[str, Any]:
    if not isinstance(uow.scope, Scope):
        raise ServiceError("SCOPE_MISMATCH", "清理必须指定业务渠道", 403)
    uow.require_lock(content_key(uow.scope))
    table = metadata.tables[name]
    repo = Repository(table, uow.scope, include_deleted=True)
    old = await repo.get(uow.connection, identifier)
    row = {
        "is_deleted": False,
        **{c.name: None for c in table.c if c.name != "is_deleted"},
        **(old or {}),
        **values,
        **scope_values(table, uow.scope),
        "id": identifier,
        "created_at": old["created_at"] if old else utcnow(),
        "updated_at": utcnow(),
        "revision": old["revision"] + 1 if old else 1,
    }
    validate_row(table, row)
    if old:
        await uow.connection.execute(
            update(table).where(repo.predicate(), table.c.id == identifier).values(**row)
        )
    else:
        if await rows(uow.connection, uow.scope.channel_id, name, id=identifier):
            raise ServiceError("SCOPE_MISMATCH", "清理记录归属不一致", 403)
        await uow.connection.execute(insert(table).values(**row))
    return row
