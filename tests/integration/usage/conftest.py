"""用量验收复用真实渠道身份、数据库与 Redis 夹具。"""

import pytest

from creativity_service.modules.usage.assembly import build_usage_services
from tests.integration.channels.conftest import channel_env, provision

__all__ = ["channel_env"]


from tests.support.file_store import FileURLStore


class MemoryStore(FileURLStore):
    def __init__(self):
        self.values = {}

    async def put(self, key, data, content_type):
        self.values[key] = data

    async def get(self, key, max_bytes):
        return self.values[key]


@pytest.fixture
async def usage_env(channel_env):
    env = channel_env
    env.channel = await provision(env)
    env.scope = env.channel.manager.context.scope
    env.context = env.channel.manager.context
    env.store = MemoryStore()
    env.usage = build_usage_services(env.engine, env.services.channels, env.store)
    env.client._transport.app.state.usage = env.usage
    return env
