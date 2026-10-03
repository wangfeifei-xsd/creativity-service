"""窗口重复、条目去重、重启与取消必须保留同一实际运行。"""

import asyncio
from datetime import timedelta

import pytest

from creativity_service.core.context import TaskEnvelope
from creativity_service.core.database import transaction
from creativity_service.core.primitives import RunInput, ServiceError, utcnow
from creativity_service.modules.integrations.automation import keys, repo
from creativity_service.modules.integrations.automation_schemas import (
    BatchCreate,
    BatchItem,
    ScheduleCreate,
    Toggle,
)
from creativity_service.workers.executor import execute_message
from tests.integration.runtime.test_execution import admitted

pytestmark = [
    pytest.mark.integration,
    pytest.mark.parametrize(
        "agent_env",
        [
            {
                "environment": "test",
                "independent_actions": ["release:publish", "data:read_sensitive", "data:export"],
            }
        ],
        indirect=True,
    ),
]


def request(env, value="批量测试"):
    return RunInput(agent_code=env.body.agent_code, input={"request": value})


async def test_batches_deduplicate_partial_failures_retry_and_cancel(runtime_env):
    env = runtime_env
    await admitted(env, purpose="production")
    service = env.runs.automation
    body = BatchCreate(
        name="同步批次",
        items=[
            BatchItem(event_id="one", request=request(env)),
            BatchItem(event_id="bad", request=request(env).model_copy(update={"input": {}})),
        ],
    )
    batches = await asyncio.gather(
        *(service.create_batch(env.context, body, "one") for _ in range(3))
    )
    assert len({b.batch_id for b in batches}) == 1
    batch = batches[0]
    await asyncio.gather(*(service.sweep(env.context.scope.channel_id) for _ in range(2)))
    value = await service.batch(env.context, batch.batch_id)
    good, bad = value.items
    assert good.state == "ADMITTED"
    assert bad.state == "FAILED"
    again = await service.create_batch(
        env.context, body.model_copy(update={"name": "另一批"}), "two"
    )
    assert again.items[0].run_id == good.run_id
    with pytest.raises(ServiceError) as conflict:
        await service.create_batch(
            env.context,
            BatchCreate(
                name="不同正文", items=[BatchItem(event_id="one", request=request(env, "其他"))]
            ),
            "three",
        )
    assert conflict.value.code == "IDEMPOTENCY_CONFLICT"
    retried = await service.retry_item(env.context, bad.item_id, bad.revision)
    assert retried.state == "PENDING"
    await service.cancel_batch(env.context, batch.batch_id, batch.revision, True)
    await service.sweep(env.context.scope.channel_id)
    assert (await env.runs.get_run(env.context, good.run_id)).state == "CANCELLED"
    assert (await service.batch(env.context, batch.batch_id)).items[1].state == "CANCELLED"
    other_scope = env.context.model_copy(
        update={
            "scope": env.context.scope.model_copy(
                update={"subject_type": "USER", "subject_id": "other"}
            )
        }
    )
    with pytest.raises(ServiceError):
        await service.get(other_scope, "automation_batches", batch.batch_id)


async def test_schedule_window_restart_pause_and_missed_history(runtime_env):
    env = runtime_env
    await admitted(env, purpose="production")
    service = env.runs.automation
    row = await service.create_schedule(
        env.context, ScheduleCreate(name="每日结果", request=request(env), interval_seconds=60)
    )
    now = utcnow()
    async with transaction(
        env.engine, env.context.scope, keys(env.context, ("automation_schedules", row["id"]))
    ) as uow:
        due = await repo(env.context.scope, "automation_schedules").change(
            uow, row["id"], row["revision"], {"next_at": now}
        )
    await asyncio.gather(*(service.fire(due, now) for _ in range(3)))
    items = await service.channel_rows(env.context.scope.channel_id, "automation_items")
    assert len(items) == 1
    await service.dispatch(items[0])
    await service.dispatch(items[0])
    item = await service.get(env.context, "automation_items", items[0]["id"])
    await execute_message(
        env.runs,
        TaskEnvelope(channel_id=env.context.scope.channel_id, run_id=item["run_id"]),
        "schedule-worker",
        env.runtime,
    )
    assert (await env.runs.get_run(env.context, item["run_id"])).state == "SUCCEEDED"
    current = await service.get(env.context, "automation_schedules", due["id"])
    await service.toggle_schedule(
        env.context, current["id"], Toggle(revision=current["revision"], active=False)
    )
    await service.fire(current, now + timedelta(hours=3))
    assert len(await service.channel_rows(env.context.scope.channel_id, "automation_items")) == 1
