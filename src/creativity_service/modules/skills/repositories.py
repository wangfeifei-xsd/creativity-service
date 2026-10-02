"""技能仓储始终绑定受信渠道，公共版本与文件表共用短事务。"""

from creativity_service.core.context import Scope
from creativity_service.core.database import Repository
from creativity_service.core.database.tables import metadata as core_metadata
from creativity_service.modules.skills.tables import metadata


def repository(name: str, scope: Scope) -> Repository:
    table = metadata.tables[name] if name in metadata.tables else core_metadata.tables[name]
    return Repository(table, scope)
