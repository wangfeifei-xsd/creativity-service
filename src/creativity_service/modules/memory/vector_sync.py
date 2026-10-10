"""向量同步意图与业务数据同事务提交，租约覆盖事务外 Milvus 调用。"""

from collections import defaultdict
from datetime import timedelta
from typing import Any

from sqlalchemy import bindparam, insert, or_, select, update
from sqlalchemy.ext.asyncio import AsyncEngine

from creativity_service.core.context import Scope
from creativity_service.core.database import Repository, UnitOfWork, transaction, validate_row
from creativity_service.core.database.soft_delete import active_rows
from creativity_service.core.deletion import ContentRef, DeletionGuard, content_key
from creativity_service.core.primitives import ServiceError, new_id, utcnow
from creativity_service.modules.memory.embedding_tables import metadata
from creativity_service.modules.memory.vector_store import MilvusStore

TASKS = metadata.tables["memory_index_tasks"]
VECTORS = metadata.tables["memory_embeddings"]


async def update_many(uow: UnitOfWork, changes: list[dict[str, Any]]) -> None:
    """同形状修改使用驱动批量参数，不按每条向量往返数据库。"""
    groups: dict[tuple[str, ...], list[dict[str, Any]]] = defaultdict(list)
    for change in changes:
        groups[tuple(sorted(set(change) - {"task_id"}))].append(change)
    for fields, rows in groups.items():
        statement = (
            update(TASKS)
            .where(
                Repository(TASKS, Scope.model_validate(uow.scope.model_dump())).predicate(),
                TASKS.c.id == bindparam("task_id"),
            )
            .values(**{field: bindparam("value_" + field) for field in fields})
        )
        parameters = [
            {"task_id": row["task_id"], **{"value_" + field: row[field] for field in fields}}
            for row in rows
        ]
        await uow.connection.execute(statement, parameters)


async def enqueue(uow: UnitOfWork, rows: list[dict[str, Any]], *, delete: bool = False) -> None:
    """固定向量标识不复用；删除意图一旦登记，迟到的写入不能将其改回。"""
    if not isinstance(uow.scope, Scope):
        raise ValueError("向量任务必须属于业务范围")
    uow.require_lock(content_key(uow.scope))
    if not rows:
        return
    existing = await Repository(TASKS, uow.scope).get_many(uow.connection, [r["id"] for r in rows])
    now = utcnow()
    additions = []
    changes = []
    for row in rows:
        old = existing.get(row["id"])
        if old:
            if not delete or old["operation"] == "DELETE":
                continue
            # 保留执行中租约，等待旧写入完成后再删，避免写删同时出站。
            changes.append(
                dict(
                    task_id=row["id"],
                    operation="DELETE",
                    revision=old["revision"] + 1,
                    state="RUNNING" if old["state"] == "RUNNING" else "PENDING",
                    next_attempt_at=now,
                    updated_at=now,
                )
            )
        else:
            task = {
                "is_deleted": False,
                "id": row["id"],
                **uow.scope.model_dump(),
                "dimensions": row["dimensions"],
                "operation": "DELETE" if delete else "UPSERT",
                "state": "PENDING",
                "lease_token": None,
                "lease_until": None,
                "attempts": 0,
                "next_attempt_at": now,
                "created_at": now,
                "updated_at": now,
                "revision": 1,
            }
            validate_row(TASKS, task)
            additions.append(task)
    await update_many(uow, changes)
    if additions:
        await uow.connection.execute(insert(TASKS), additions)


class VectorSync:
    def __init__(self, engine: AsyncEngine, store: MilvusStore | None = None) -> None:
        self.engine, self.store = engine, store or MilvusStore()

    async def sync(self, scope: Scope, ids: list[str] | None = None) -> None:
        token, now = new_id("vector_lease"), utcnow()
        async with transaction(self.engine, scope, [content_key(scope)]) as uow:
            statement = active_rows(
                select(TASKS).where(
                    Repository(TASKS, scope).predicate(),
                    TASKS.c.next_attempt_at <= now,
                    or_(
                        TASKS.c.state == "PENDING",
                        TASKS.c.lease_until <= now,
                        (TASKS.c.state == "SYNCED") & (TASKS.c.operation == "DELETE"),
                    ),
                )
            )
            if ids is not None:
                statement = statement.where(TASKS.c.id.in_(ids))
            jobs = [
                dict(r)
                for r in (
                    await uow.connection.execute(
                        statement.order_by(TASKS.c.next_attempt_at, TASKS.c.id).limit(100)
                    )
                ).mappings()
            ]
            if not jobs:
                return
            identifiers = [r["id"] for r in jobs]
            cached = await Repository(VECTORS, scope).get_many(uow.connection, identifiers)
            blocked = await DeletionGuard(scope).blocked_refs(
                uow, [ContentRef("memory_embedding", row["id"]) for row in cached.values()]
            )
            # 旧索引来源已撤销时把任务转为删除，不把排队写入重新发送到 Milvus。
            for job in jobs:
                if ContentRef("memory_embedding", job["id"]) in blocked:
                    job["operation"] = "DELETE"
            if blocked:
                await uow.connection.execute(
                    update(TASKS)
                    .where(
                        Repository(TASKS, scope).predicate(),
                        TASKS.c.id.in_([ref.resource_id for ref in blocked]),
                    )
                    .values(operation="DELETE")
                )

            await uow.connection.execute(
                update(TASKS)
                .where(
                    Repository(TASKS, scope).predicate(),
                    TASKS.c.id.in_(identifiers),
                )
                .values(state="RUNNING", lease_token=token, lease_until=now + timedelta(minutes=5))
            )
        failure = None
        try:
            groups: dict[tuple[str, int], list[dict[str, Any]]] = defaultdict(list)
            for job in jobs:
                operation = job["operation"] if job["id"] in cached else "DELETE"
                groups[operation, job["dimensions"]].append(job)
            for (operation, dimensions), batch in groups.items():
                if operation == "UPSERT":
                    await self.store.upsert(scope, dimensions, [cached[r["id"]] for r in batch])
                else:
                    await self.store.delete(scope, dimensions, [r["id"] for r in batch])
        except Exception as exc:
            failure = exc
        async with transaction(self.engine, scope, [content_key(scope)]) as uow:
            latest = await Repository(TASKS, scope).get_many(uow.connection, identifiers)
            changes = []
            for job in jobs:
                current = latest[job["id"]]
                changed = current["revision"] != job["revision"] or current["lease_token"] != token
                # 过期执行迟到或写入期间发生遗忘：保留删除意图并重新调度。
                state = "PENDING" if failure or changed else "SYNCED"
                delay = min(300, 2 ** min(current["attempts"], 8)) if failure else 0
                if state == "SYNCED" and current["operation"] == "DELETE":
                    # 删除墓碑定期再次清扫，覆盖进程崩溃后服务端迟到的写入。
                    delay = 3600
                values = dict(
                    state=state,
                    lease_token=None,
                    lease_until=None,
                    updated_at=utcnow(),
                    next_attempt_at=utcnow() + timedelta(seconds=delay),
                    attempts=current["attempts"] + 1 if failure else 0,
                )
                if current["lease_token"] != token and current["state"] == "RUNNING":
                    # 不解除其他执行者的租约；其完成时依据修订变化再次调度。
                    values = dict(revision=current["revision"] + 1, updated_at=utcnow())
                changes.append({"task_id": job["id"], **values})
            await update_many(uow, changes)
        if failure:
            raise ServiceError(
                "VECTOR_UNAVAILABLE", "Milvus 同步失败，后台将自动重试", 503
            ) from failure

    async def require_synced(self, scope: Scope, ids: list[str], operation: str = "UPSERT") -> None:
        if not ids:
            return
        async with self.engine.connect() as connection:
            tasks = await Repository(TASKS, scope).get_many(connection, ids)
        if any(
            i not in tasks or tasks[i]["state"] != "SYNCED" or tasks[i]["operation"] != operation
            for i in ids
        ):
            raise ServiceError("VECTOR_UNAVAILABLE", "记忆向量仍在同步，请稍后重试", 503)

    async def sweep(self, channel_id: str) -> None:
        if not channel_id or channel_id == "system":
            raise ValueError("向量补偿必须指定业务渠道")
        async with self.engine.connect() as connection:
            rows = (
                (
                    await connection.execute(
                        active_rows(
                            select(TASKS)
                            .where(
                                TASKS.c.channel_id == channel_id,
                                TASKS.c.next_attempt_at <= utcnow(),
                                or_(
                                    TASKS.c.state == "PENDING",
                                    TASKS.c.lease_until <= utcnow(),
                                    (TASKS.c.state == "SYNCED") & (TASKS.c.operation == "DELETE"),
                                ),
                            )
                            .order_by(TASKS.c.next_attempt_at, TASKS.c.id)
                            .limit(100)
                        )
                    )
                )
                .mappings()
                .all()
            )
        scopes = {
            Scope.model_validate({key: row[key] for key in Scope.model_fields}) for row in rows
        }
        for scope in scopes:
            try:
                await self.sync(scope)
            except ServiceError:
                # 单个主体的网络失败已落库，不阻断其他主体的补偿。
                continue
