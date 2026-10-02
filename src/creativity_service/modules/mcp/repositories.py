"""全部连接数据按受信渠道和环境读取。"""

from creativity_service.core.context import Scope
from creativity_service.core.database import Repository
from creativity_service.modules.mcp.tables import metadata


def repository(scope: Scope, table: str) -> Repository:
    return Repository(metadata.tables[table], scope)
