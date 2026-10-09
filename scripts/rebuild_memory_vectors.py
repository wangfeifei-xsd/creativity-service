"""按渠道分页重登记向量同步任务；不调用模型，不覆盖删除意图。"""

import argparse
import asyncio
from collections import defaultdict
from typing import Any

from sqlalchemy import select, update

from creativity_service.core.config import Settings
from creativity_service.core.context import Scope
from creativity_service.core.database import Repository, transaction
from creativity_service.core.deletion import ContentRef, DeletionGuard, content_key
from creativity_service.core.infrastructure import Infrastructure
from creativity_service.core.primitives import utcnow
from creativity_service.modules.memory.vector_sync import TASKS, VECTORS, enqueue


async def rebuild(channel_id: str) -> int:
    if not channel_id or channel_id == "system":
        raise ValueError("必须指定业务渠道")
    infrastructure = Infrastructure(Settings())
    count, cursor = 0, ""
    try:
        while True:
            async with infrastructure.engine.connect() as connection:
                rows = [
                    dict(r)
                    for r in (
                        await connection.execute(
                            select(VECTORS)
                            .where(VECTORS.c.channel_id == channel_id, VECTORS.c.id > cursor)
                            .order_by(VECTORS.c.id)
                            .limit(100)
                        )
                    ).mappings()
                ]
            if not rows:
                break
            grouped: dict[Scope, list[dict[str, Any]]] = defaultdict(list)
            for row in rows:
                grouped[Scope.model_validate({key: row[key] for key in Scope.model_fields})].append(
                    row
                )
            for scope, batch in grouped.items():
                async with transaction(infrastructure.engine, scope, [content_key(scope)]) as uow:
                    cached = await Repository(VECTORS, scope).get_many(
                        uow.connection, [r["id"] for r in batch]
                    )
                    blocked = await DeletionGuard(scope).blocked_refs(
                        uow, [ContentRef("memory_embedding", i) for i in cached]
                    )
                    valid = [
                        row
                        for row in cached.values()
                        if ContentRef("memory_embedding", row["id"]) not in blocked
                    ]
                    await enqueue(uow, valid)
                    await uow.connection.execute(
                        update(TASKS)
                        .where(
                            Repository(TASKS, scope).predicate(),
                            TASKS.c.id.in_([r["id"] for r in valid]),
                            TASKS.c.operation == "UPSERT",
                            TASKS.c.state != "RUNNING",
                        )
                        .values(state="PENDING", attempts=0, next_attempt_at=utcnow())
                    )
                    count += len(valid)
            cursor = rows[-1]["id"]
    finally:
        await infrastructure.close()
    return count


def main() -> None:
    parser = argparse.ArgumentParser(description="从 MySQL 缓存按渠道重建 Milvus 索引")
    parser.add_argument("--channel", required=True)
    args = parser.parse_args()
    print(f"已重新登记 {asyncio.run(rebuild(args.channel))} 条向量同步任务")


if __name__ == "__main__":
    main()
