"""Web、Worker 和补偿入口共用的 PostgreSQL 事务互斥协议。"""

from dataclasses import dataclass, replace
from functools import cached_property
from hashlib import sha256

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

from creativity_service.core.primitives import canonical_json


@dataclass(frozen=True)
class ResourceKey:
    channel_id: str
    resource_type: str
    business_key: tuple[str, ...]
    shared: bool = False

    def __post_init__(self) -> None:
        if not self.channel_id or not self.resource_type or not self.business_key:
            raise ValueError("锁键必须包含渠道、资源类型与业务键")
        if any(not isinstance(part, str) or not part for part in self.business_key):
            raise ValueError("锁键分量必须为非空字符串")

    @cached_property
    def lock_id(self) -> int:
        payload = ["lock-v1", self.channel_id, self.resource_type, list(self.business_key)]
        return int.from_bytes(sha256(canonical_json(payload)).digest()[:8], "big", signed=True)


def record_key(channel_id: str, table: str, record_id: str) -> ResourceKey:
    return ResourceKey(channel_id, f"record:{table}", (record_id,))


def read_key(key: ResourceKey) -> ResourceKey:
    """读取可并行，但仍与同一资源的修改、撤销及删除互斥。"""
    return replace(key, shared=True)


def lock_order(key: ResourceKey) -> tuple[int, int]:
    # 额度总在业务资源之后获取，允许在同一事务末尾完成短暂的配额检查和登记。
    rank = {"usage-ledger": 1, "usage-platform-limits": 2}.get(key.resource_type, 0)
    return rank, key.lock_id


def normalize_keys(keys: frozenset[ResourceKey]) -> frozenset[ResourceKey]:
    """同一资源同时声明读写时只取排他锁，禁止先读后升级造成死锁。"""
    chosen: dict[ResourceKey, ResourceKey] = {}
    for key in keys:
        identity = replace(key, shared=False)
        previous = chosen.get(identity)
        if previous is None or not key.shared:
            chosen[identity] = key
    return frozenset(chosen.values())


async def acquire_locks(connection: AsyncConnection, keys: frozenset[ResourceKey]) -> None:
    # 所有入口使用同一顺序；共享锁只用于只读资源，写入仍要求对应排他锁。
    ordered = sorted(normalize_keys(keys), key=lock_order)
    modes: dict[int, bool] = {}
    for key in ordered:
        modes[key.lock_id] = modes.get(key.lock_id, True) and key.shared
    if ordered:
        result = await connection.execute(
            text(
                "SELECT current_setting('transaction_isolation'), "
                "CASE WHEN shared THEN pg_advisory_xact_lock_shared(lock_id) "
                "ELSE pg_advisory_xact_lock(lock_id) END "
                "FROM unnest(CAST(:keys AS bigint[]), CAST(:shared AS boolean[])) "
                "AS locks(lock_id, shared)"
            ),
            {"keys": list(modes), "shared": list(modes.values())},
        )
        # 隔离级别与取锁共用一次往返，仍检查当前真实事务，不能只信任引擎默认配置。
        if result.scalar() != "read committed":
            raise RuntimeError("事务互斥协议要求 READ COMMITTED 隔离级别")
