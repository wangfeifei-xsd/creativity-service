"""接入仓储始终保留渠道和环境；主体委托仅在验签后读取映射。"""

from typing import Any

from sqlalchemy.ext.asyncio import AsyncConnection

from creativity_service.core.context import Scope
from creativity_service.core.database import Repository
from creativity_service.core.locking import ResourceKey
from creativity_service.core.primitives import ServiceError
from creativity_service.modules.channels.repositories import one
from creativity_service.modules.integrations.tables import metadata


def repository(scope: Scope, name: str) -> Repository:
    return Repository(metadata.tables[name], scope)


def environment_scope(scope: Scope) -> Scope:
    return Scope(channel_id=scope.channel_id, environment=scope.environment)


def configuration_key(scope: Scope) -> ResourceKey:
    return ResourceKey(scope.channel_id, "integration-config", (scope.environment,))


async def source_mapping(
    connection: AsyncConnection, scope: Scope, kind: str, external_id: str
) -> dict[str, Any]:
    row = await one(
        connection,
        "data_scopes",
        scope.channel_id,
        environment=scope.environment,
        external_scope_type=kind,
        external_scope_id=external_id,
    )
    if row is None or row["status"] != "ACTIVE":
        raise ServiceError("DELEGATION_SCOPE_INVALID", "业务主体数据域不可用", 403)
    return row
