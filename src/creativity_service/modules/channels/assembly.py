"""渠道与 IAM 的生产装配，后续模块在同一实例登记查询和生命周期能力。"""

from dataclasses import dataclass

from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncEngine

from creativity_service.modules.channels.keys import KeyService
from creativity_service.modules.channels.lifecycle import LifecycleService
from creativity_service.modules.channels.repositories import ChannelRepository
from creativity_service.modules.channels.resources import ResourceCatalog
from creativity_service.modules.channels.services import ChannelService
from creativity_service.modules.channels.state import (
    ChannelDirectory,
    ChannelResourceReader,
    ChannelStateService,
    ServiceIdentityService,
)
from creativity_service.modules.iam.services import IamServices, build_iam_services


@dataclass(frozen=True)
class ChannelServices:
    channels: ChannelService
    keys: KeyService
    lifecycle: LifecycleService


def build_channel_services(
    engine: AsyncEngine,
    redis: Redis,
    prefix: str,
    *,
    management_ttl: int = 28800,
    service_ttl: int = 3600,
) -> tuple[IamServices, ChannelServices]:
    repository = ChannelRepository(engine)
    iam = build_iam_services(
        engine,
        redis,
        prefix,
        management_ttl=management_ttl,
        service_ttl=service_ttl,
        channels=ChannelStateService(repository),
        services=ServiceIdentityService(repository),
        directory=ChannelDirectory(repository),
        resources=ChannelResourceReader(repository),
    )
    channels = ChannelService(repository, iam, resources=ResourceCatalog(engine, iam.authorization))
    return iam, ChannelServices(channels, KeyService(channels), LifecycleService(channels))
