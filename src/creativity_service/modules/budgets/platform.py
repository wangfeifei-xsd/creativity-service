"""平台配置只取当前版本，占用按适用状态及周期在数据库中聚合。"""

from datetime import datetime
from typing import Any

from sqlalchemy import and_, func, or_, select
from sqlalchemy.ext.asyncio import AsyncConnection

from creativity_service.modules.usage.tables import metadata


async def current_limits(connection: AsyncConnection, now: datetime) -> list[dict[str, Any]]:
    table = metadata.tables["platform_limits"]
    latest = (
        select(
            table,
            func.row_number()
            .over(
                partition_by=table.c.limit_code,
                order_by=(table.c.created_at.desc(), table.c.id.desc()),
            )
            .label("position"),
            func.count().over(partition_by=table.c.limit_code).label("versions"),
        )
        .where(table.c.channel_id == "system", table.c.effective_at <= now)
        .subquery()
    )
    result = []
    for row in (await connection.execute(select(latest).where(latest.c.position == 1))).mappings():
        item = dict(row)
        item.pop("position")
        item["revision"] = item.pop("versions")
        result.append(item)
    return result


async def occupancy_counts(
    connection: AsyncConnection, limits: list[dict[str, Any]], starts: dict[str, datetime]
) -> dict[str, int]:
    table = metadata.tables["platform_quota_occupancies"]
    predicates = [
        and_(
            table.c.limit_code == limit["limit_code"],
            table.c.status == "HELD"
            if limit["unit"] == "concurrency"
            else table.c.created_at >= starts[limit["limit_code"]],
        )
        for limit in limits
        if limit["status"] == "ACTIVE"
    ]
    if not predicates:
        return {}
    result = await connection.execute(
        select(table.c.limit_code, func.count())
        .where(table.c.channel_id == "system", or_(*predicates))
        .group_by(table.c.limit_code)
    )
    return {code: int(count) for code, count in result}
