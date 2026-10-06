"""管理工作区可保存智能体草稿，锁内复核仍拒绝业务执行和伪造范围。"""

from unittest.mock import AsyncMock

import pytest

from creativity_service.core.database import transaction
from creativity_service.core.primitives import ServiceError, new_id
from creativity_service.modules.agents.access import locked_require
from creativity_service.modules.agents.registry import templates
from creativity_service.modules.channels.repositories import management_scope_id
from creativity_service.modules.channels.schemas import EnvironmentUpdate
from creativity_service.modules.iam.schemas import ChannelContextInput
from creativity_service.modules.skills.schemas import SkillCreate, SkillTestInput
from tests.integration.channels.conftest import login

pytestmark = pytest.mark.integration


async def test_management_agent_draft_and_locked_boundaries(agent_env):
    env = agent_env
    channel_id = env.context.scope.channel_id
    environment = env.context.scope.environment
    await env.services.channels.enable_management_workspace(env.admin, channel_id, environment)
    _, session = await login(env)
    token = await env.iam.sessions.enter(
        session,
        ChannelContextInput(
            channel_id=channel_id,
            environment=environment,
            data_scope_id=management_scope_id(channel_id, environment),
        ),
    )
    manager = await env.iam.authentication.admin_session(token.access_token, new_id("request"))
    context = manager.context
    body = env.body.model_copy(update={"definition": templates()[0].definition})
    detail = await env.agents.create(context, body)
    assert detail.agent.name == body.name
    assert len(detail.versions) == 1 and detail.versions[0].status.value == "DRAFT"
    # 管理范围的加载验证不能在保存成功后又尝试创建业务运行。
    runner = AsyncMock()
    env.skills.runtime_runner = runner
    skill = await env.skills.create(
        context,
        SkillCreate(
            skill_code="load_check",
            name="加载验证",
            description="验证管理范围",
            owner="测试",
            instructions="简洁回答。",
        ),
    )
    version = skill.versions[0]
    loaded = await env.skills.test(
        context, version.version_id, SkillTestInput(revision=version.revision)
    )
    assert loaded.result.complete and loaded.run_id is None
    runner.submit.assert_not_called()
    assert (await env.skills.tests(context, version.version_id))[0].test_id == loaded.test_id
    async with transaction(env.engine, context.scope, env.agents.keys(context, "new")) as uow:
        await locked_require(uow, context, "agent:manage", "agent", "new")
        with pytest.raises(ServiceError, match="授权不足"):
            await locked_require(uow, context, "run:create", "agent", "new")
    forged = [
        context.model_copy(update={"principal_type": "worker"}),
        context.model_copy(
            update={"scope": context.scope.model_copy(update={"data_scope_id": "manage_forged"})}
        ),
        context.model_copy(
            update={
                "scope": context.scope.model_copy(
                    update={"data_scope_id": management_scope_id(channel_id, "prod")}
                )
            }
        ),
    ]
    for candidate in forged:
        async with transaction(
            env.engine, candidate.scope, env.agents.keys(candidate, "new")
        ) as uow:
            with pytest.raises(ServiceError, match="业务数据域不可用"):
                await locked_require(uow, candidate, "agent:manage", "agent", "new")
    await env.services.channels.update_environment(
        env.admin, channel_id, environment, EnvironmentUpdate(revision=1, status="DISABLED")
    )
    async with transaction(env.engine, context.scope, env.agents.keys(context, "new")) as uow:
        with pytest.raises(ServiceError, match="渠道或目标环境不可用"):
            await locked_require(uow, context, "agent:manage", "agent", "new")
