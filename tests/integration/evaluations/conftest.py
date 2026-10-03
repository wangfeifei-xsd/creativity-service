"""复用真实运行环境并装配生产评测服务。"""

import pytest

from creativity_service.modules.evaluations.assembly import build_evaluation_service
from tests.integration.agents.conftest import agent_env as agent_env
from tests.integration.channels.conftest import channel_env as channel_env
from tests.integration.runtime.conftest import runtime_env as runtime_env


@pytest.fixture
async def evaluation_env(runtime_env):
    env = runtime_env
    env.evaluations = build_evaluation_service(
        env.engine, env.iam.authorization, env.agents, env.runs, env.cleanup
    )
    env.client._transport.app.state.evaluations = env.evaluations
    return env
