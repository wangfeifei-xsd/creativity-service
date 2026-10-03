"""到期扫描按原渠道策略登记删除；暂停与归档本身不冒充内容清理。"""

from datetime import timedelta
from typing import Any

from sqlalchemy import delete, select

from creativity_service.core.context import Scope
from creativity_service.core.database import transaction
from creativity_service.core.deletion import content_key
from creativity_service.core.deletion.ledger import DeletionLedger
from creativity_service.core.primitives import ServiceError, utcnow
from creativity_service.modules.channels.schemas import RetentionPolicy
from creativity_service.modules.data_lifecycle.graph import TARGETS
from creativity_service.modules.data_lifecycle.repository import rows, scope_of
from creativity_service.modules.data_lifecycle.services import DataLifecycleService
from creativity_service.modules.usage.repositories import ledger_key
from creativity_service.storage import metadata


async def scan_retention(
    service: DataLifecycleService, channel_id: str, limit: int = 100
) -> dict[str, int]:
    if not 1 <= limit <= 500:
        raise ValueError("保留期扫描批量必须在 1 到 500 之间")
    Scope(channel_id=channel_id, environment="dev")
    async with service.engine.connect() as connection:
        channels = await rows(connection, channel_id, "channels", id=channel_id)
        if not channels:
            raise ServiceError("NOT_FOUND", "渠道不存在", 404)
        channel = channels[0]
        policy = RetentionPolicy.model_validate(channel["retention_policy"])
        markers = await rows(connection, channel_id, "deletion_markers")
        marked = {(r["target_type"], r["target_id"]) for r in markers}
        selected: list[tuple[str, dict[str, Any]]] = []
        for kind, name in TARGETS.items():
            if (
                kind not in {"run", "conversation", "artifact", "memory", "usage_export"}
                and channel["status"] != "ARCHIVED"
            ):
                continue
            for row in await rows(connection, channel_id, name):
                if (kind, row["id"]) in marked:
                    continue
                days = (
                    policy.retention_days
                    if kind == "conversation"
                    else policy.export_days
                    if kind == "usage_export"
                    else policy.run_content_days
                )
                deadline = (
                    row["expires_at"]
                    if kind == "memory"
                    else row["created_at"] + timedelta(days=days)
                )
                if kind == "artifact" and row["state"] in {"STAGED", "DELETING", "DELETED"}:
                    deadline = min(
                        row["upload_expires_at"],
                        row["created_at"] + timedelta(hours=policy.temporary_hours),
                    )
                elif row.get("expires_at"):
                    deadline = min(deadline, row["expires_at"])
                if kind not in {"run", "conversation", "artifact", "memory", "usage_export"}:
                    deadline = channel["archived_at"] + timedelta(days=policy.retention_days)
                if deadline <= utcnow():
                    selected.append((kind, row))
    selected.sort(key=lambda item: (item[1]["created_at"], item[0], item[1]["id"]))
    for kind, row in selected[:limit]:
        await DeletionLedger().record(
            scope_of(row), kind, row["id"], "RETENTION_EXPIRED", "data_lifecycle"
        )
    await service.import_ledger(channel_id)
    removed = 0
    scope = Scope(channel_id=channel_id, environment="dev")
    async with transaction(
        service.engine, scope, [content_key(scope), ledger_key(channel_id)]
    ) as uow:
        events = metadata.tables["run_events"]
        old = await uow.connection.execute(
            select(events.c.id, events.c.payload_ref)
            .where(
                events.c.channel_id == channel_id,
                (events.c.expires_at <= utcnow())
                | (events.c.created_at <= utcnow() - timedelta(hours=policy.sse_hours)),
            )
            .order_by(events.c.created_at)
            .limit(limit)
        )
        for event in old.mappings():
            contents = metadata.tables["run_contents"]
            await uow.connection.execute(
                delete(contents).where(
                    contents.c.channel_id == channel_id,
                    contents.c.id == event["payload_ref"],
                    contents.c.kind == "event",
                )
            )
            await uow.connection.execute(
                delete(events).where(events.c.channel_id == channel_id, events.c.id == event["id"])
            )
            removed += 1
        # 元数据账本按自身时间清理，删除标记、证明与尚待结算用量不在此范围。
        for name in ("audit_events", "usage_aggregates"):
            table = metadata.tables.get(name)
            if table is None:
                continue
            found = await uow.connection.execute(
                select(table.c.id)
                .where(
                    table.c.channel_id == channel_id,
                    table.c.updated_at <= utcnow() - timedelta(days=policy.metadata_days),
                )
                .order_by(table.c.created_at)
                .limit(limit)
            )
            identifiers = list(found.scalars())
            if identifiers:
                await uow.connection.execute(
                    delete(table).where(
                        table.c.channel_id == channel_id, table.c.id.in_(identifiers)
                    )
                )
                removed += len(identifiers)
        records = metadata.tables["usage_records"]
        expired = await uow.connection.execute(
            select(records.c.id, records.c.attempt_id)
            .where(
                records.c.channel_id == channel_id,
                records.c.state.in_(["SETTLED", "RELEASED"]),
                records.c.updated_at <= utcnow() - timedelta(days=policy.metadata_days),
            )
            .order_by(records.c.updated_at)
            .limit(limit)
        )
        for record in expired.mappings():
            for name, field, identifier in (
                ("usage_events", "attempt_id", record["attempt_id"]),
                ("usage_adjustments", "usage_id", record["id"]),
                ("budget_reservations", "attempt_id", record["attempt_id"]),
                ("usage_records", "id", record["id"]),
            ):
                table = metadata.tables[name]
                await uow.connection.execute(
                    delete(table).where(
                        table.c.channel_id == channel_id, table.c[field] == identifier
                    )
                )
            removed += 1
    from creativity_service.modules.data_lifecycle.objects import sweep_objects

    objects = await sweep_objects(service, channel_id, policy.temporary_hours, limit)
    if service.handlers.redis is not None:
        from creativity_service.modules.tools.execution import RedisToolCache

        cache = RedisToolCache(service.handlers.redis, service.handlers.prefix, service.engine)
        prefix = f"{service.handlers.prefix}:tools:"
        async for key in service.handlers.redis.scan_iter(
            match=f"{prefix}{channel_id}:*", count=100
        ):
            await cache.get((key.decode() if isinstance(key, bytes) else key).removeprefix(prefix))
    return {
        "registered": min(len(selected), limit),
        "metadata_removed": removed,
        "orphan_objects": objects,
    }
