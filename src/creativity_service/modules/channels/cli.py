"""显式初始化系统渠道，不生成业务渠道或凭据。"""

import argparse
import asyncio

from creativity_service.core.config import Settings
from creativity_service.core.infrastructure import Infrastructure
from creativity_service.modules.channels.assembly import build_channel_services


async def initialize() -> None:
    settings = Settings()
    infrastructure = Infrastructure(settings)
    try:
        _, services = build_channel_services(
            infrastructure.engine,
            infrastructure.redis_clients["redis_auth"],
            settings.redis_key_prefix,
        )
        await services.channels.initialize_system()
        print("系统渠道已初始化")
    finally:
        await infrastructure.close()


def main() -> None:
    parser = argparse.ArgumentParser(description="渠道初始化管理")
    parser.add_argument("command", choices=["init-system"])
    parser.parse_args()
    asyncio.run(initialize())


if __name__ == "__main__":
    main()
