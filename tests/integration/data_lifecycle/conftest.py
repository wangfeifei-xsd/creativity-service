"""生命周期验收复用真实 PostgreSQL、会话与记忆运行环境。"""

import pytest

from creativity_service.modules.data_lifecycle.handlers import ContentHandlers
from creativity_service.modules.data_lifecycle.services import DataLifecycleService
from tests.integration.memory.conftest import (
    authorization,
    context,
    database_schema,
    engine,
    env,
    run_env,
    validator,
)

__all__ = ["authorization", "context", "database_schema", "engine", "env", "run_env", "validator"]


@pytest.fixture
async def lifecycle(env):
    service = DataLifecycleService(
        env.engine,
        ContentHandlers(env.engine, env.conversations.artifacts.store, env.runs),
        env.authorization,
    )
    return service
