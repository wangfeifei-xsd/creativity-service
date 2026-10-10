"""提示词读写使用公共范围仓储，所有写操作依赖服务层事务和锁。"""

from creativity_service.core.context import Scope
from creativity_service.core.database import Repository
from creativity_service.core.database.tables import metadata as core_metadata
from creativity_service.modules.prompts.tables import metadata


def repository(name: str, scope: Scope, *, include_deleted: bool = False) -> Repository:
    table = metadata.tables[name] if name in metadata.tables else core_metadata.tables[name]
    return Repository(table, scope, include_deleted=include_deleted)
