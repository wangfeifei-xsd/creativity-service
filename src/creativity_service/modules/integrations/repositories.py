"""接入仓储始终保留渠道和环境；主体委托仅在验签后读取映射。"""

from creativity_service.core.context import Scope
from creativity_service.core.database import Repository
from creativity_service.core.locking import ResourceKey
from creativity_service.modules.integrations.tables import metadata


def repository(scope: Scope, name: str) -> Repository:
    return Repository(metadata.tables[name], scope)


def environment_scope(scope: Scope) -> Scope:
    return Scope(channel_id=scope.channel_id, environment=scope.environment)


def configuration_key(scope: Scope) -> ResourceKey:
    return ResourceKey(scope.channel_id, "integration-config", (scope.environment,))
