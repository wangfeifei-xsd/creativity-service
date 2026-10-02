"""真实 PostgreSQL、运行服务及受控对象存储替身。"""

import pytest

from creativity_service.core.artifacts import ArtifactService
from creativity_service.core.database import assert_external_io_allowed
from creativity_service.modules.conversations.schemas import AgentChoice, ConversationCreate
from creativity_service.modules.conversations.services import ConversationService
from tests.integration.core.conftest import (
    authorization,
    context,
    database_schema,
    engine,
    validator,
)
from tests.integration.runs.conftest import env as run_env

__all__ = ["authorization", "context", "database_schema", "engine", "validator", "run_env"]


class Store:
    def __init__(self):
        self.data = {}

    async def put(self, key, data, content_type):
        assert_external_io_allowed()
        self.data[key] = data

    async def get(self, key, max_bytes):
        assert_external_io_allowed()
        return self.data[key]

    async def delete(self, key):
        assert_external_io_allowed()
        self.data.pop(key, None)


class Directory:
    async def list_available(self, context):
        return [AgentChoice(agent_code="test_agent", name="内部测试任务")]


@pytest.fixture
async def env(run_env):
    env = run_env
    env.store = Store()
    env.conversations = ConversationService(
        env.engine,
        env.authorization,
        env.runs,
        ArtifactService(env.engine, env.store, env.authorization),
        directory=Directory(),
    )
    env.runs.turns = env.conversations.hooks
    env.conversation = await env.conversations.create(
        env.context, ConversationCreate(agent_code="test_agent")
    )
    env.cid = env.conversation.conversation_id
    return env
