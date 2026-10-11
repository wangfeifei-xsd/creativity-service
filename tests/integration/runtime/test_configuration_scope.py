"""渠道配置范围支持固定模型验证，业务执行及原文访问仍需要真实数据授权。"""

import pytest
from sqlalchemy import select, update

from creativity_service.core.context import TaskEnvelope
from creativity_service.core.primitives import RunInput
from creativity_service.modules.iam.schemas import ChannelContextInput
from creativity_service.modules.models.schemas import TestInput as Cases
from creativity_service.modules.runs.repositories import required
from creativity_service.storage import metadata
from creativity_service.workers.executor import execute_message
from tests.integration.agents.test_agents import publish
from tests.integration.channels.conftest import login

pytestmark = pytest.mark.integration


async def configuration_session(env):
    channel_id = env.context.scope.channel_id
    _, session = await login(env)
    token = await env.iam.sessions.enter(
        session,
        ChannelContextInput(
            channel_id=channel_id,
            environment="test",
        ),
    )
    return await env.iam.authentication.admin_session(token.access_token, "configuration-test")


async def test_configuration_model_validation_uses_runtime_and_preserves_channel_permissions(
    runtime_env,
):
    env = runtime_env
    agent = await env.agents.create(env.context, env.body)
    await publish(env, agent)
    manager = await configuration_session(env)
    context = manager.context
    test = await env.models.testing.create(manager, env.model.id, Cases(cases=["text", "usage"]))
    assert test.run_id, test.model_dump_json()
    await execute_message(
        env.runs,
        TaskEnvelope(channel_id=context.scope.channel_id, run_id=test.run_id),
        "worker",
        env.runtime,
    )
    result = await env.models.testing.get(manager, test.id)
    async with env.engine.connect() as db:
        row = await required(db, "runs", context.scope.channel_id, id=test.run_id)
        assert row["state"] == "SUCCEEDED", row["error"]
        assert result.state == "FIXTURE", result.model_dump_json()
        assert len(result.attempt_ids) == 2 and len(env.adapter.calls) == 2
        events = (
            (
                await db.execute(
                    select(metadata.tables["usage_events"]).where(
                        metadata.tables["usage_events"].c.channel_id == context.scope.channel_id,
                        metadata.tables["usage_events"].c.attempt_id.in_(result.attempt_ids),
                    )
                )
            )
            .mappings()
            .all()
        )
        assert len(events) == 2
    # 现行渠道管理权包含运行原文，其他敏感内容仍须独立授权。
    assert (await env.runs.get_run(context, test.run_id)).state == "SUCCEEDED"
    assert not (await env.iam.authorization.check(context, "memory:read", "memory", "new")).allowed
    for resource in ("agent", "tool", "prompt", "skill"):
        assert (await env.iam.authorization.check(context, "run:create", resource, "new")).allowed
    receipt = await env.runs.admit_run(
        context, RunInput(agent_code=env.body.agent_code, input={"request": "业务输入"}), "business"
    )
    assert receipt.run_id != test.run_id
    assert len(env.adapter.calls) == 2


async def test_configuration_model_validation_rechecks_permissions_before_worker_call(runtime_env):
    env = runtime_env
    manager = await configuration_session(env)
    context = manager.context
    test = await env.models.testing.create(manager, env.model.id, Cases(cases=["text"]))
    assert test.run_id
    grants = metadata.tables["resource_grants"]
    async with env.engine.begin() as db:
        await db.execute(
            update(grants)
            .where(grants.c.channel_id == context.scope.channel_id)
            .values(allowed_actions=[])
        )
    await execute_message(
        env.runs,
        TaskEnvelope(channel_id=context.scope.channel_id, run_id=test.run_id),
        "worker",
        env.runtime,
    )
    assert env.adapter.calls == []
    async with env.engine.connect() as db:
        row = await required(db, "runs", context.scope.channel_id, id=test.run_id)
        assert row["state"] == "FAILED", row
