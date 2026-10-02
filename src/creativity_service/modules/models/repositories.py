"""模型仓储复用渠道范围、修订冲突及普通索引约定。"""

from typing import Any

from sqlalchemy.ext.asyncio import AsyncConnection

from creativity_service.core.context import Scope
from creativity_service.core.database import Repository
from creativity_service.core.database.tables import metadata as core_metadata
from creativity_service.core.locking import ResourceKey
from creativity_service.core.primitives import ServiceError
from creativity_service.modules.models.tables import metadata


def model_key(channel_id: str) -> ResourceKey:
    # 所有配置写入及执行前读取共用渠道模型锁，防止检查后并发改绑。
    return ResourceKey(channel_id, "model-policy", ("channel",))


def repository(scope: Scope, name: str) -> Repository:
    table = metadata.tables.get(name)
    if table is None:
        table = core_metadata.tables[name]
    return Repository(table, scope)


async def required(
    connection: AsyncConnection, scope: Scope, name: str, record_id: str
) -> dict[str, Any]:
    row = await repository(scope, name).get(connection, record_id)
    if row is None:
        raise ServiceError("NOT_FOUND", "模型资源不存在", 404)
    return row
