"""导出及预占补偿定时任务，只从持久化渠道目录和任务中恢复范围。"""

import asyncio

from celery import shared_task

from creativity_service.core.artifacts import S3ObjectStore
from creativity_service.core.config import Settings
from creativity_service.core.context import Scope
from creativity_service.core.infrastructure import Infrastructure
from creativity_service.modules.channels.assembly import build_channel_services
from creativity_service.modules.channels.repositories import rows as channel_rows
from creativity_service.modules.usage.assembly import build_usage_services
from creativity_service.modules.usage.repositories import rows


async def sweep() -> None:
    settings = Settings()
    infrastructure = Infrastructure(settings)
    try:
        _, channels = build_channel_services(
            infrastructure.engine,
            infrastructure.redis_clients["redis_auth"],
            settings.redis_key_prefix,
        )
        services = build_usage_services(
            infrastructure.engine,
            channels.channels,
            S3ObjectStore(infrastructure.s3, infrastructure.bucket),
        )
        async with infrastructure.engine.connect() as connection:
            channel_ids = await channels.channels.repository.directory(connection)
        for channel_id in ["system", *channel_ids]:
            async with infrastructure.engine.connect() as connection:
                environments = await channel_rows(connection, "channel_environments", channel_id)
                exports = await rows(connection, "usage_exports", channel_id)
            if environments:
                scope = Scope(
                    channel_id=channel_id,
                    environment=environments[0]["environment"],
                )
                await services.ledger.compensate(scope)
            for job in exports:
                if job["state"] in {"QUEUED", "RUNNING"}:
                    await services.exports.process(channel_id, job["id"])
    finally:
        await infrastructure.close()


def sweep_usage() -> None:
    asyncio.run(sweep())


sweep_usage_task = shared_task(name="usage.sweep", ignore_result=True)(sweep_usage)
