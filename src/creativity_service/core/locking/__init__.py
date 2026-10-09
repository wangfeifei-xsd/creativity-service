"""Web、Worker 和补偿入口共用的 MySQL InnoDB 事务互斥协议。"""

import json
from dataclasses import dataclass, replace
from functools import cached_property
from hashlib import sha256
from itertools import groupby

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
        rank = {"usage-ledger": 1, "usage-platform-limits": 2}.get(self.resource_type, 0)
        return (
            rank * 4096 + int.from_bytes(sha256(canonical_json(payload)).digest()[:8], "big") % 4096
        )


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
    if not ordered:
        return
    isolation = await connection.scalar(text("SELECT @@transaction_isolation"))
    if isolation != "READ-COMMITTED":
        raise RuntimeError("事务互斥协议要求 READ COMMITTED 隔离级别")
    # 固定锁槽只由初始化脚本写入，碰撞只增加等待，不会绕过互斥。
    # 有序参数表驱动精确索引查找，禁止优化器退化成扫描整个系统渠道。
    # IN 查询即使 FORCE INDEX，也可能临时锁住不匹配行，破坏全局锁顺序。
    for shared, group in groupby(modes.items(), key=lambda item: item[1]):
        slots = [slot for slot, _ in group]
        statement = text(
            "SELECT /*+ NO_BKA(locked) NO_BNL(locked) */ locked.slot "
            "FROM JSON_TABLE(:slots, '$[*]' COLUMNS (slot BIGINT PATH '$')) AS requested "
            "STRAIGHT_JOIN transaction_lock_slots AS locked "
            "FORCE INDEX (ix_transaction_lock_slots_0) "
            "ON locked.channel_id='system' AND locked.slot=requested.slot "
            "ORDER BY requested.slot "
            + ("FOR SHARE OF locked" if shared else "FOR UPDATE OF locked")
        )
        found = list((await connection.scalars(statement, {"slots": json.dumps(slots)})).all())
        if found != slots:
            raise RuntimeError("事务锁槽缺失或重复，请修复初始化数据")
