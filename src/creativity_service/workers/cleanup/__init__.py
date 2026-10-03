"""清理 Worker 从渠道目录与持久化记录恢复范围，消息不能覆盖主体或渠道。"""

import asyncio
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from celery import shared_task

from creativity_service.core.artifacts import S3ObjectStore
from creativity_service.core.config import Settings
from creativity_service.core.infrastructure import Infrastructure
from creativity_service.modules.channels.assembly import build_channel_services
from creativity_service.modules.channels.lifecycle import LifecycleService
from creativity_service.modules.data_lifecycle.handlers import ContentHandlers
from creativity_service.modules.data_lifecycle.retention import scan_retention
from creativity_service.modules.data_lifecycle.services import DataLifecycleService
from creativity_service.modules.runtime.bootstrap import worker_services

logger = logging.getLogger(__name__)


@asynccontextmanager
async def runtime() -> AsyncIterator[tuple[DataLifecycleService, list[str], LifecycleService]]:
    settings = Settings()
    infrastructure = Infrastructure(settings)
    try:
        iam, channels = build_channel_services(
            infrastructure.engine,
            infrastructure.redis_clients["redis_auth"],
            settings.redis_key_prefix,
        )
        runs = worker_services(infrastructure, iam)
        service = DataLifecycleService(
            infrastructure.engine,
            ContentHandlers(
                infrastructure.engine,
                S3ObjectStore(infrastructure.s3, infrastructure.bucket),
                runs,
                infrastructure.redis_clients["redis_cache"],
                settings.redis_key_prefix,
            ),
            iam.authorization,
        )
        async with infrastructure.engine.connect() as connection:
            channel_ids = await channels.channels.repository.directory(connection)
        yield service, channel_ids, channels.lifecycle
    finally:
        await infrastructure.close()


async def sweep() -> None:
    async with runtime() as (service, channel_ids, lifecycle):
        for channel_id in channel_ids:
            try:
                events = await lifecycle.pending(channel_id, "retention")
                await scan_retention(service, channel_id)
                await service.sweep(channel_id)
                for event in events:
                    await lifecycle.acknowledge(channel_id, event.event_id, "retention")
            except Exception:
                # 日志只含定位信息；内容、凭据与第三方异常文本不进入清理日志。
                logger.error("渠道清理暂未完成", extra={"channel_id": channel_id})


def sweep_cleanup() -> None:
    asyncio.run(sweep())


sweep_cleanup_task = shared_task(name="cleanup.sweep", ignore_result=True)(sweep_cleanup)
