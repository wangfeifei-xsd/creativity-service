"""完整运行表存在时，渠道归档真实检查任务并与受理互斥。"""

import asyncio

import pytest

from creativity_service.core.primitives import RunInput, ServiceError
from creativity_service.modules.channels.repositories import rows as channel_rows
from creativity_service.modules.runs.assembly import RunLifecycleGuard
from creativity_service.modules.runs.repositories import rows
from creativity_service.modules.runs.schemas import TERMINAL
from tests.integration.agents.test_agents import publish
from tests.integration.runtime.test_execution import admitted

pytestmark = pytest.mark.integration


async def test_archive_counts_actual_runs_and_allows_terminal_history(runtime_env):
    env = runtime_env
    env.services.lifecycle.register_tasks(RunLifecycleGuard())
    channel_id = env.context.scope.channel_id
    receipt, _ = await admitted(env, purpose="production")
    impact = await env.services.lifecycle.preview(env.admin, channel_id, "archive")
    assert not impact.can_execute and impact.unfinished_tasks == 1
    with pytest.raises(ServiceError, match="未终结"):
        await env.services.lifecycle.change(
            env.admin, channel_id, "archive", env.tenant.channel.revision
        )
    await env.runs.cancel(env.context, receipt.run_id)
    impact = await env.services.lifecycle.preview(env.admin, channel_id, "archive")
    assert impact.can_execute and impact.unfinished_tasks == 0
    changed = await env.services.lifecycle.change(
        env.admin, channel_id, "archive", env.tenant.channel.revision
    )
    assert changed.status == "ARCHIVED"


async def test_archive_racing_admission_never_leaves_active_run_in_archived_channel(runtime_env):
    env = runtime_env
    env.services.lifecycle.register_tasks(RunLifecycleGuard())
    detail = await env.agents.create(env.context, env.body)
    await publish(env, detail)
    channel_id = env.context.scope.channel_id
    outcomes = await asyncio.gather(
        env.runs.admit_run(
            env.context,
            RunInput(agent_code=env.body.agent_code, input={"request": "请处理"}),
            "race",
        ),
        env.services.lifecycle.change(
            env.admin, channel_id, "archive", env.tenant.channel.revision
        ),
        return_exceptions=True,
    )
    assert sum(isinstance(value, ServiceError) for value in outcomes) == 1
    async with env.engine.connect() as connection:
        channel = (await channel_rows(connection, "channels", channel_id))[0]
        active = [
            r for r in await rows(connection, "runs", channel_id) if r["state"] not in TERMINAL
        ]
    assert not (channel["status"] == "ARCHIVED" and active)
