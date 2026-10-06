"""管理工作区可保存智能体草稿，锁内复核仍拒绝业务执行和伪造范围。"""

import pytest

from creativity_service.core.database import transaction
from creativity_service.core.primitives import ServiceError, new_id
from creativity_service.modules.agents.access import locked_require
from creativity_service.modules.agents.registry import templates
from creativity_service.modules.channels.schemas import EnvironmentUpdate
from creativity_service.modules.iam.schemas import ChannelContextInput
from tests.integration.channels.conftest import login

pytestmark = pytest.mark.integration


async def test_management_agent_draft_and_locked_boundaries(agent_env):
    env = agent_env
    channel_id = env.context.scope.channel_id
    environment = env.context.scope.environment
    _, session = await login(env)
    token = await env.iam.sessions.enter(
        session,
        ChannelContextInput(
            channel_id=channel_id,
            environment=environment,
        ),
    )
    manager = await env.iam.authentication.admin_session(token.access_token, new_id("request"))
    context = manager.context
    body = env.body.model_copy(update={"definition": templates()[0].definition})
    detail = await env.agents.create(context, body)
    assert detail.agent.name == body.name
    assert len(detail.versions) == 1 and detail.versions[0].status.value == "DRAFT"
    async with transaction(env.engine, context.scope, env.agents.keys(context, "new")) as uow:
        await locked_require(uow, context, "agent:manage", "agent", "new")
        await locked_require(uow, context, "run:create", "agent", "new")
    forged = context.model_copy(
        update={"scope": context.scope.model_copy(update={"environment": "prod"})}
    )
    async with transaction(env.engine, forged.scope, env.agents.keys(forged, "new")) as uow:
        with pytest.raises(ServiceError, match="渠道或目标环境不可用"):
            await locked_require(uow, forged, "agent:manage", "agent", "new")
    await env.services.channels.update_environment(
        env.admin, channel_id, environment, EnvironmentUpdate(revision=1, status="DISABLED")
    )
    async with transaction(env.engine, context.scope, env.agents.keys(context, "new")) as uow:
        with pytest.raises(ServiceError, match="渠道或目标环境不可用"):
            await locked_require(uow, context, "agent:manage", "agent", "new")
