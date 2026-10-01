"""Web、Worker 和补偿入口共用的 PostgreSQL 事务互斥协议。"""

from dataclasses import dataclass
from hashlib import sha256

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

from creativity_service.core.primitives import canonical_json


@dataclass(frozen=True)
class ResourceKey:
    channel_id: str
    resource_type: str
    business_key: tuple[str, ...]

    def __post_init__(self) -> None:
        if not self.channel_id or not self.resource_type or not self.business_key:
            raise ValueError("锁键必须包含渠道、资源类型与业务键")
        if any(not isinstance(part, str) or not part for part in self.business_key):
            raise ValueError("锁键分量必须为非空字符串")

    @property
    def lock_id(self) -> int:
        payload = ["lock-v1", self.channel_id, self.resource_type, list(self.business_key)]
        return int.from_bytes(sha256(canonical_json(payload)).digest()[:8], "big", signed=True)


def record_key(channel_id: str, table: str, record_id: str) -> ResourceKey:
    return ResourceKey(channel_id, f"record:{table}", (record_id,))


async def acquire_locks(connection: AsyncConnection, keys: frozenset[ResourceKey]) -> None:
    # 按有符号整数统一排序并去重；摘要碰撞仅增加互斥，不削弱隔离。
    for lock_id in sorted({key.lock_id for key in keys}):
        await connection.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": lock_id})
