"""用量查询、资源引用与任务归档的模块登记端口。"""

from typing import Protocol

from sqlalchemy.ext.asyncio import AsyncConnection

from creativity_service.core.auth.authentication import AdminSession
from creativity_service.core.context import Scope
from creativity_service.core.locking import ResourceKey
from creativity_service.modules.channels.schemas import ResourceReference, UsageQuery, UsageView


class UsageReader(Protocol):
    async def query(self, channel_id: str, scopes: list[Scope], query: UsageQuery) -> UsageView: ...


class ResourceReferenceReader(Protocol):
    async def references(
        self, session: AdminSession, channel_id: str
    ) -> list[ResourceReference]: ...


class TaskLifecycleGuard(Protocol):
    """11 的受理及终结必须共用此锁；实现只使用当前数据库连接。"""

    def keys(self, channel_id: str) -> list[ResourceKey]: ...
    async def unfinished(self, connection: AsyncConnection, channel_id: str) -> int: ...
