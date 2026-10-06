"""批量统计当前页的环境配置，仅作入口提示，不代替工作区授权。"""

from collections.abc import Sequence

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncConnection

from creativity_service.core.context import Scope
from creativity_service.modules.channels.schemas import ChannelConfigurationStatus
from creativity_service.modules.channels.tables import metadata


async def configuration_counts(
    connection: AsyncConnection,
    channel_ids: Sequence[str],
    *,
    scope: Scope | None = None,
) -> dict[str, dict[str, int]]:
    """仅统计当前页启用环境；渠道环境只展示当前环境。"""
    result: dict[str, dict[str, int]] = {channel_id: {} for channel_id in channel_ids}
    if not channel_ids:
        return result
    environments = metadata.tables["channel_environments"]
    filters = [environments.c.channel_id.in_(channel_ids), environments.c.status == "ACTIVE"]
    if scope:
        filters.append(environments.c.environment == scope.environment)
    enabled = (
        select(environments.c.channel_id, environments.c.environment)
        .where(*filters)
        .cte("configured_environments")
    )
    statement = select(enabled.c.channel_id, func.count()).group_by(enabled.c.channel_id)
    for channel_id, count in (await connection.execute(statement)).all():
        result[channel_id]["environments"] = count
    return result


def configuration_status(
    channel_id: str, counts: dict[str, int], allowed: set[str]
) -> list[ChannelConfigurationStatus]:
    """只提示进入工作区所需的环境；接入服务及 Key 按需配置。"""
    if "channel:govern" not in allowed and "environment:manage" not in allowed:
        return []
    count = counts.get("environments", 0)
    return [
        ChannelConfigurationStatus(
            key="environments",
            label="环境",
            completed=count > 0,
            message=f"已配置：{count} 个启用环境" if count else "未配置：没有启用环境",
            path=f"/channels/{channel_id}?tab=environments",
        )
    ]
