"""窗口重复、条目去重、重启与取消必须保留同一实际运行。"""

import asyncio
from datetime import timedelta

import pytest

from creativity_service.core.context import TaskEnvelope
from creativity_service.core.database import transaction
from creativity_service.core.deletion import ContentRef, DeletionService
from creativity_service.core.primitives import RunInput, ServiceError, utcnow
from creativity_service.modules.agents.repositories import repository as agent_repository
from creativity_service.modules.data_lifecycle.handlers import ContentHandlers
from creativity_service.modules.data_lifecycle.services import DataLifecycleService
from creativity_service.modules.integrations.automation import keys, repo
from creativity_service.modules.integrations.automation_schemas import (
    BatchCreate,
    BatchItem,
    ScheduleCreate,
    Toggle,
)
from creativity_service.workers.executor import execute_message
from tests.integration.agents.test_agents import publish
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


async def test_schedule_requires_current_published_agent(runtime_env):
    env = runtime_env
    detail = await env.agents.create(env.context, env.body)
    service = env.runs.automation
    body = ScheduleCreate(name="发布状态边界", request=request(env), interval_seconds=60)
    with pytest.raises(ServiceError) as unpublished:
        await service.create_schedule(env.context, body)
    assert unpublished.value.code == "AGENT_NOT_RELEASED"
    assert not (await env.agents.list_agents(env.context, published_only=True)).items

    await publish(env, detail)
    assert len((await env.agents.list_agents(env.context, published_only=True)).items) == 1
    row = await service.create_schedule(env.context, body)
    row = await service.toggle_schedule(
        env.context, row["id"], Toggle(revision=row["revision"], active=False)
    )
    mapping_id = env.agents.mapping_id(env.context, detail.agent.agent_id)
    async with transaction(
        env.engine,
        env.context.scope,
        env.agents.keys(env.context, detail.agent.agent_id, ("release_mappings", mapping_id)),
    ) as uow:
        mappings = agent_repository("release_mappings", env.context.scope)
        mapping = await mappings.get(uow.connection, mapping_id)
        assert mapping
        await mappings.change(uow, mapping_id, mapping["revision"], {"is_deleted": True})
    with pytest.raises(ServiceError) as removed:
        await service.toggle_schedule(
            env.context, row["id"], Toggle(revision=row["revision"], active=True)
        )
    assert removed.value.code == "AGENT_NOT_RELEASED"
    assert not (await env.agents.list_agents(env.context, published_only=True)).items
    assert (await service.get(env.context, "automation_schedules", row["id"]))["state"] == "PAUSED"


async def test_schedule_rejects_invalid_input_on_create_and_resume(runtime_env):
    env = runtime_env
    await admitted(env, purpose="production")
    service = env.runs.automation
    body = ScheduleCreate(name="输入边界", request=request(env), interval_seconds=60)
    invalid = body.model_copy(update={"request": request(env).model_copy(update={"input": {}})})
    with pytest.raises(ServiceError) as rejected:
        await service.create_schedule(env.context, invalid)
    assert rejected.value.code == "INPUT_SCHEMA_INVALID"
    assert not await service.list_schedules(env.context)

    row = await service.create_schedule(env.context, body)
    row = await service.toggle_schedule(
        env.context, row["id"], Toggle(revision=row["revision"], active=False)
    )
    # 模拟修复前已保存的无效计划；重新启用同样必须按当前发布结构检查。
    async with transaction(
        env.engine, env.context.scope, keys(env.context, ("automation_schedules", row["id"]))
    ) as uow:
        row = await repo(env.context.scope, "automation_schedules").change(
            uow, row["id"], row["revision"], {"spec": invalid.model_dump(mode="json")}
        )
    with pytest.raises(ServiceError) as resumed:
        await service.toggle_schedule(
            env.context, row["id"], Toggle(revision=row["revision"], active=True)
        )
    assert resumed.value.code == "INPUT_SCHEMA_INVALID"
    assert (await service.get(env.context, "automation_schedules", row["id"]))["state"] == "PAUSED"


async def test_deleted_run_keeps_batch_queryable_and_other_items_cancellable(runtime_env):
    env = runtime_env
    await admitted(env, purpose="production")
    service = env.runs.automation
    body = BatchCreate(
        name="部分删除批次",
        items=[BatchItem(event_id=key, request=request(env)) for key in ("deleted", "retained")],
    )
    batch = await service.create_batch(env.context, body, "deletion-batch")
    await service.sweep(env.context.scope.channel_id)
    deleted, retained = (await service.batch(env.context, batch.batch_id)).items
    # 标记提交即不可复用；此时清理任务还没有清空批次条目。
    await DeletionService(env.engine, env.iam.authorization).mark(
        env.context, ContentRef("run", deleted.run_id), "TEST"
    )
    current = await service.batch(env.context, batch.batch_id)
    assert current.items[0].state == "DELETED"
    assert current.items[0].run_id is None and current.items[0].error is None
    assert current.items[1].run_id == retained.run_id
    assert [item.batch_id for item in await service.list_batches(env.context)] == [batch.batch_id]
    cancelled = await service.cancel_batch(env.context, batch.batch_id, current.revision, True)
    assert cancelled.items[0].state == "DELETED"
    assert (await env.runs.get_run(env.context, retained.run_id)).state == "CANCELLED"

    lifecycle = DataLifecycleService(
        env.engine,
        ContentHandlers(env.engine, env.conversations.artifacts.store, env.runs),
        env.iam.authorization,
    )
    await lifecycle.prepare_channel(env.context.scope.channel_id)
    for _ in range(10):
        if await lifecycle.sweep(env.context.scope.channel_id, 100) == 0:
            break
    async with env.engine.connect() as db:
        assert await repo(env.context.scope, "automation_items").get(db, deleted.item_id) is None
        cleaned = await repo(env.context.scope, "automation_items", include_deleted=True).get(
            db, deleted.item_id
        )
        assert cleaned["state"] == "DELETED" and cleaned["request"] == {}
    assert (await service.batch(env.context, batch.batch_id)).items[0].state == "DELETED"
    with pytest.raises(ServiceError) as denied:
        await service.retry_item(env.context, deleted.item_id, cleaned["revision"])
    assert denied.value.status == 404
