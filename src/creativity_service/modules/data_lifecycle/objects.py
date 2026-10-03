"""对象扫描只处理当前渠道无有效元数据的对象，覆盖上传进程退出留下的孤儿。"""

from datetime import timedelta

from creativity_service.core.artifacts import S3ObjectStore
from creativity_service.core.primitives import utcnow
from creativity_service.modules.data_lifecycle.repository import rows
from creativity_service.modules.data_lifecycle.services import DataLifecycleService


async def sweep_objects(
    service: DataLifecycleService,
    channel_id: str,
    temporary_hours: int = 1,
    limit: int = 100,
    *,
    restoring: bool = False,
) -> int:
    store = service.handlers.store
    if not isinstance(store, S3ObjectStore):
        return 0
    cursor, cleaned = None, 0
    while True:
        objects, cursor = await store.list_page(f"channels/{channel_id}/", cursor)
        async with service.engine.connect() as connection:
            artifacts = await rows(connection, channel_id, "artifacts")
            exports = await rows(connection, channel_id, "usage_exports")
        known = {r["object_key"] for r in artifacts if r["state"] in {"STAGED", "AVAILABLE"}}
        known.update(r["object_key"] for r in exports if r["state"] == "SUCCEEDED")
        for key, modified_at in objects:
            if key in known or (
                not restoring and modified_at > utcnow() - timedelta(hours=temporary_hours)
            ):
                continue
            await store.delete(key)
            cleaned += 1
            if cleaned >= limit:
                return cleaned
        if cursor is None:
            return cleaned
