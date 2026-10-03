"""恢复前关闭独立屏障，取得最新清单并重放全部清理后才允许开放。"""

from typing import Any

from sqlalchemy import delete

from creativity_service.core.database import transaction
from creativity_service.core.deletion import content_key
from creativity_service.core.deletion.ledger import DeletionLedger, maintenance_mode
from creativity_service.core.primitives import ServiceError, digest, utcnow
from creativity_service.modules.data_lifecycle.repository import put, rows, scope_of
from creativity_service.modules.data_lifecycle.services import DataLifecycleService
from creativity_service.storage import metadata


async def backup_manifest(service: DataLifecycleService, channel_id: str) -> dict[str, Any]:
    ledger = DeletionLedger()
    await ledger.operate(channel_id, initialize=True)
    async with service.engine.connect() as connection:
        markers = await rows(connection, channel_id, "deletion_markers")
    for marker in markers:
        await ledger.record(
            scope_of(marker),
            marker["target_type"],
            marker["target_id"],
            marker["reason_code"],
            marker["requested_by"],
        )
    manifest = await ledger.operate(channel_id, initialize=True)
    return {
        "format": "creativity-deletion-manifest-v1",
        "observed_at": utcnow().isoformat(),
        "manifest": manifest,
        "digest": digest(manifest),
    }


async def block_restore(channel_id: str) -> dict[str, Any]:
    # 独立屏障先落盘，恢复旧数据库不会将此状态回滚为开放。
    return await DeletionLedger().operate(channel_id, blocked=True, required=True)


async def replay_restore(
    service: DataLifecycleService, channel_id: str, minimum_sequence: int
) -> dict[str, Any]:
    ledger = DeletionLedger()
    manifest = await ledger.operate(channel_id, required=True)
    if not manifest["blocked"] or manifest["sequence"] < minimum_sequence:
        raise ServiceError(
            "RECOVERY_PROOF_INVALID", "恢复须先关闭入口并取得满足水位的最新清单", 503
        )
    token = maintenance_mode.set(True)
    try:
        await service.import_ledger(channel_id, required=True)
        async with service.engine.connect() as connection:
            barriers = await rows(connection, channel_id, "recovery_barriers")
            jobs = await rows(connection, channel_id, "deletion_jobs")
        for barrier in barriers:
            scope = scope_of(barrier)
            async with transaction(service.engine, scope, [content_key(scope)]) as uow:
                await put(
                    uow,
                    "recovery_barriers",
                    barrier["id"],
                    {"state": "BLOCKED", "verified_at": None},
                )
        for job in jobs:
            scope = scope_of(job)
            async with transaction(service.engine, scope, [content_key(scope)]) as uow:
                await put(
                    uow,
                    "deletion_jobs",
                    job["id"],
                    {"state": "PENDING", "affected_resources": [], "completed_at": None},
                )
                for name in ("deletion_work_items", "deletion_receipts"):
                    table = metadata.tables[name]
                    await uow.connection.execute(
                        delete(table).where(
                            table.c.channel_id == channel_id, table.c.job_id == job["id"]
                        )
                    )
        for _ in range(1000):
            count = await service.sweep(channel_id, 500)
            async with service.engine.connect() as connection:
                items = await rows(connection, channel_id, "deletion_work_items")
            if any(i["state"] == "FAILED" for i in items):
                raise ServiceError(
                    "RECOVERY_CLEANUP_FAILED", "恢复清理未完成，业务入口继续关闭", 503
                )
            if count == 0:
                break
        async with service.engine.connect() as connection:
            jobs = await rows(connection, channel_id, "deletion_jobs")
            proofs = await rows(connection, channel_id, "deletion_receipts")
        if any(j["state"] != "COMPLETED" for j in jobs):
            raise ServiceError("RECOVERY_CLEANUP_PENDING", "恢复清理尚未完成", 503)
        from creativity_service.modules.data_lifecycle.objects import sweep_objects

        while await sweep_objects(service, channel_id, limit=500, restoring=True):
            pass
        latest = await ledger.operate(channel_id, required=True)
        if digest(latest) != digest(manifest):
            raise ServiceError(
                "RECOVERY_PROOF_INVALID", "恢复期间清单或封锁状态已变化，请重新核对", 503
            )
        for barrier in barriers:
            scope = scope_of(barrier)
            async with transaction(service.engine, scope, [content_key(scope)]) as uow:
                await put(
                    uow,
                    "recovery_barriers",
                    barrier["id"],
                    {
                        "state": "READY",
                        "marker_digest": digest(
                            sorted(
                                e["id"]
                                for e in latest["entries"]
                                if e["scope"] == scope.model_dump()
                            )
                        ),
                        "verified_at": utcnow(),
                    },
                )
        # 水位比较与开放在同一文件锁中，核对失败不会打开独立屏障。
        await ledger.operate(
            channel_id,
            required=True,
            blocked=False,
            expected_sequence=latest["sequence"],
            expected_digest=digest(latest),
        )
        return {
            "format": "creativity-cleanup-proof-v1",
            "channel_id": channel_id,
            "sequence": latest["sequence"],
            "manifest_digest": digest(latest),
            "proof_digests": [p["proof_digest"] for p in proofs],
            "verified_at": utcnow().isoformat(),
        }
    finally:
        maintenance_mode.reset(token)
