"""窗口重复、条目去重、重启与取消必须保留同一实际运行。"""

import asyncio
from datetime import timedelta

import pytest

from creativity_service.core.context import TaskEnvelope
from creativity_service.core.database import transaction
from creativity_service.core.deletion import ContentRef, DeletionService
from creativity_service.core.primitives import RunInput, ServiceError, utcnow
from creativity_service.modules.agents.lifecycle import AgentLifecycle
from creativity_service.modules.agents.schemas import AgentStateInput
from creativity_service.modules.data_lifecycle.handlers import ContentHandlers
from creativity_service.modules.data_lifecycle.services import DataLifecycleService
from creativity_service.modules.integrations.automation import keys, repo
from creativity_service.modules.integrations.automation_schemas import (
    BatchCreate,
    BatchItem,
    ScheduleCreate,
    ScheduleUpdate,
    Toggle,
)
from creativity_service.modules.integrations.automation_tables import metadata
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


async def test_schedule_requires_online_enabled_agent(runtime_env):
    env = runtime_env
    detail = await env.agents.create(env.context, env.body)
    service = env.runs.automation
    body = ScheduleCreate(name="上线状态边界", request=request(env), interval_seconds=60)
    with pytest.raises(ServiceError) as unpublished:
        await service.create_schedule(env.context, body)
    assert unpublished.value.code == "AGENT_OFFLINE"
    assert not (await env.agents.list_agents(env.context, online_only=True)).items

    detail = await publish(env, detail)
    assert len((await env.agents.list_agents(env.context, online_only=True)).items) == 1
    row = await service.create_schedule(env.context, body)
    row = await service.toggle_schedule(
        env.context, row["id"], Toggle(revision=row["revision"], active=False)
    )
    await AgentLifecycle(env.agents).state(
        env.context,
        detail.agent.agent_id,
        AgentStateInput(revision=detail.agent.revision, operation="offline", reason="暂停接收任务"),
    )
    with pytest.raises(ServiceError) as removed:
        await service.toggle_schedule(
            env.context, row["id"], Toggle(revision=row["revision"], active=True)
        )
    assert removed.value.code == "AGENT_OFFLINE"
    assert not (await env.agents.list_agents(env.context, online_only=True)).items
    assert (await service.get(env.context, "automation_schedules", row["id"]))["state"] == "PAUSED"
    with pytest.raises(ServiceError) as edited:
        await service.update_schedule(
            env.context, row["id"], ScheduleUpdate(**body.model_dump(), revision=row["revision"])
        )
    assert edited.value.code == "AGENT_OFFLINE"


@pytest.mark.parametrize("active", [True, False])
async def test_schedule_edit_preserves_state_and_replaces_only_future_windows(runtime_env, active):
    env = runtime_env
    await admitted(env, purpose="production")
    service = env.runs.automation
    row = await service.create_schedule(
        env.context, ScheduleCreate(name="原计划", request=request(env), interval_seconds=60)
    )
    now = utcnow()
    async with transaction(
        env.engine, env.context.scope, keys(env.context, ("automation_schedules", row["id"]))
    ) as uow:
        due = await repo(env.context.scope, "automation_schedules").change(
            uow, row["id"], row["revision"], {"next_at": now}
        )
    await service.fire(due, now)
    current = await service.get(env.context, "automation_schedules", row["id"])
    if not active:
        await service.toggle_schedule(
            env.context, row["id"], Toggle(revision=current["revision"], active=False)
        )
        current = await service.get(env.context, "automation_schedules", row["id"])
    renamed = await service.update_schedule(
        env.context,
        row["id"],
        ScheduleUpdate(
            name="新计划",
            request=request(env, "新输入"),
            interval_seconds=60,
            revision=current["revision"],
        ),
    )
    assert renamed["next_at"] == current["next_at"]
    update = ScheduleUpdate(
        name="每日计划",
        request=request(env, "新输入"),
        daily_at="09:30",
        timezone="America/New_York",
        revision=renamed["revision"],
    )
    edited = await service.update_schedule(env.context, row["id"], update)
    saved = await service.get(env.context, "automation_schedules", row["id"])
    assert edited["state"] == ("ACTIVE" if active else "PAUSED")
    assert edited["name"] == "每日计划"
    assert edited["spec"] == update.model_dump(mode="json", exclude={"revision"})
    assert edited["next_at"] > now
    assert edited["next_at"] != renamed["next_at"]
    assert saved["identity"] == current["identity"]
    assert saved["owner_key"] == current["owner_key"]
    with pytest.raises(ServiceError) as stale:
        await service.update_schedule(env.context, row["id"], update)
    assert stale.value.code == "REVISION_CONFLICT"
    # 已扫描的旧配置不得再派发；原有条目保留原输入，新窗口使用新输入。
    await service.fire(current, current["next_at"])
    items = await service.channel_rows(env.context.scope.channel_id, "automation_items")
    assert len(items) == 1
    assert items[0]["request"]["input"] == request(env).input
    await service.fire(saved, saved["next_at"])
    items = await service.channel_rows(env.context.scope.channel_id, "automation_items")
    assert len(items) == (2 if active else 1)
    if active:
        assert any(item["request"]["input"] == {"request": "新输入"} for item in items)


async def test_schedule_edit_rejects_invalid_input_and_other_owner(runtime_env):
    env = runtime_env
    await admitted(env, purpose="production")
    service = env.runs.automation
    body = ScheduleCreate(name="输入边界", request=request(env), interval_seconds=60)
    row = await service.create_schedule(env.context, body)
    update = ScheduleUpdate(**body.model_dump(), revision=row["revision"])
    with pytest.raises(ServiceError) as rejected:
        await service.update_schedule(
            env.context,
            row["id"],
            update.model_copy(update={"request": request(env).model_copy(update={"input": {}})}),
        )
    assert rejected.value.code == "INPUT_SCHEMA_INVALID"
    assert (await service.get(env.context, "automation_schedules", row["id"]))["revision"] == row[
        "revision"
    ]
    # 模拟同渠道另一人的计划，管理权限不能越过计划所有权。
    async with transaction(
        env.engine, env.context.scope, keys(env.context, ("automation_schedules", row["id"]))
    ) as uow:
        foreign = await repo(env.context.scope, "automation_schedules").change(
            uow, row["id"], row["revision"], {"owner_key": "other-owner"}
        )
    with pytest.raises(ServiceError) as denied:
        await service.update_schedule(
            env.context, row["id"], update.model_copy(update={"revision": foreign["revision"]})
        )
    assert denied.value.code == "NOT_FOUND"
    async with env.engine.connect() as connection:
        saved = await repo(env.context.scope, "automation_schedules").get(connection, row["id"])
    assert saved["revision"] == foreign["revision"]


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


async def scheduled_item(env, schedule_id, *, dispatch=True):
    service = env.runs.automation
    async with transaction(
        env.engine, env.context.scope, keys(env.context, ("automation_schedules", schedule_id))
    ) as uow:
        schedules = repo(env.context.scope, "automation_schedules")
        row = await schedules.get(uow.connection, schedule_id)
        row = await schedules.change(uow, schedule_id, row["revision"], {"next_at": utcnow()})
    await service.fire(row, row["next_at"])
    items = await service.channel_rows(env.context.scope.channel_id, "automation_items")
    item = max((i for i in items if i["schedule_id"] == schedule_id), key=lambda i: i["created_at"])
    if dispatch:
        await service.dispatch(item)
    return await service.get(env.context, "automation_items", item["id"])


async def finish_scheduled(env, item, state):
    envelope = TaskEnvelope(channel_id=env.context.scope.channel_id, run_id=item["run_id"])
    if state == "SUCCEEDED":
        await execute_message(env.runs, envelope, "schedule-test", env.runtime)
    elif state == "CANCELLED":
        await env.runs.cancel(env.context, item["run_id"])
    elif state == "TIMED_OUT":
        from tests.integration.runs.test_runs import update_run

        await update_run(
            env, item["run_id"], "runs", item["run_id"], deadline=utcnow() - timedelta(seconds=1)
        )
        assert await env.runs.claim_lease(envelope, "schedule-test") is None
    else:
        lease = await env.runs.claim_lease(envelope, "schedule-test")
        assert lease
        await env.runs.finish_run(
            lease,
            "FAILED",
            failure=ServiceError(
                "MODEL_INPUT_INVALID", "模型请求参数不正确：max_tokens 超出供应商上限", 502
            ),
        )
    assert (await env.runs.get_run(env.context, item["run_id"])).state == state


async def test_schedule_failure_streak_resets_and_pauses_before_next_dispatch(runtime_env):
    env = runtime_env
    await admitted(env, purpose="production")
    service = env.runs.automation
    row = await service.create_schedule(
        env.context,
        ScheduleCreate(
            name="失败保护", request=request(env), interval_seconds=60, failure_threshold=2
        ),
    )
    for state, expected in (
        ("FAILED", 1),
        ("SUCCEEDED", 0),
        ("FAILED", 1),
        ("CANCELLED", 1),
        ("TIMED_OUT", 2),
    ):
        item = await scheduled_item(env, row["id"])
        await finish_scheduled(env, item, state)
        await asyncio.gather(*(service.sweep(env.context.scope.channel_id) for _ in range(3)))
        current = await service.get(env.context, "automation_schedules", row["id"])
        assert current["consecutive_failures"] == expected
        todos = await service.schedule_todos(env.context)
        assert todos.total == (1 if state == "TIMED_OUT" else 0)
    assert current["state"] == "PAUSED"
    assert current["last_error"]["code"] == "SCHEDULE_FAILURE_LIMIT"
    assert current["last_error"]["run_id"] == item["run_id"]
    assert "连续失败 2 次" in current["last_error"]["message"]
    assert todos.items[0].id == row["id"]
    assert todos.items[0].run_id == item["run_id"]
    assert todos.items[0].failure_threshold == 2
    before = await service.channel_rows(env.context.scope.channel_id, "automation_items")
    await service.fire(current, current["next_at"])
    assert len(await service.channel_rows(env.context.scope.channel_id, "automation_items")) == len(
        before
    )
    resumed = await service.toggle_schedule(
        env.context, row["id"], Toggle(revision=current["revision"], active=True)
    )
    assert resumed["consecutive_failures"] == 0 and resumed["last_error"] is None
    assert (await service.schedule_todos(env.context)).total == 0
    item = await scheduled_item(env, row["id"])
    await finish_scheduled(env, item, "FAILED")
    await service.sweep(env.context.scope.channel_id)
    current = await service.get(env.context, "automation_schedules", row["id"])
    assert current["state"] == "ACTIVE" and current["consecutive_failures"] == 1


async def test_schedule_todos_are_bounded_and_scoped(runtime_env):
    env = runtime_env
    await admitted(env, purpose="production")
    service = env.runs.automation
    row = await service.create_schedule(
        env.context, ScheduleCreate(name="待办筛选", request=request(env), interval_seconds=60)
    )
    current = await service.get(env.context, "automation_schedules", row["id"])
    base = {
        **current,
        "state": "PAUSED",
        "consecutive_failures": 3,
        "last_error": {"code": "SCHEDULE_FAILURE_LIMIT", "message": "连续失败，供应商超时"},
    }
    # 构造不同可见范围，确保分页与总数都只包含当前身份的有效自动暂停计划。
    variants = [
        {},
        {},
        {},
        {},
        {},
        {},
        {"state": "ACTIVE"},
        {"last_error": None},
        {"is_deleted": True},
        {"channel_id": "other-channel"},
        {"environment": "prod"},
        {"subject_id": "other-subject"},
        {"owner_key": "other-owner"},
    ]
    table = metadata.tables["automation_schedules"]
    async with env.engine.begin() as connection:
        await connection.execute(
            table.insert(),
            [{**base, "id": f"todo_{index}", **variant} for index, variant in enumerate(variants)],
        )
    first = await service.schedule_todos(env.context, 1, 5)
    second = await service.schedule_todos(env.context, 2, 5)
    assert first.total == second.total == 6
    assert len(first.items) == 5 and len(second.items) == 1
    assert {item.id for item in first.items + second.items} == {f"todo_{i}" for i in range(6)}
    assert all(item.run_id is None for item in first.items)
    assert (await service.schedule_todos(env.context, 3, 5)).items == []
    assert len(await service.list_schedules(env.context, "todo_0")) == 1
    assert await service.list_schedules(env.context, "todo_9") == []
    with pytest.raises(ServiceError) as denied:
        await service.schedule_todos(env.context.model_copy(update={"actor_id": None}))
    assert denied.value.status == 403


async def test_schedule_counts_trigger_order_and_does_not_dispatch_after_limit(runtime_env):
    env = runtime_env
    await admitted(env, purpose="production")
    service = env.runs.automation
    row = await service.create_schedule(
        env.context,
        ScheduleCreate(
            name="并发完成", request=request(env), interval_seconds=60, failure_threshold=2
        ),
    )
    first = await scheduled_item(env, row["id"])
    second = await scheduled_item(env, row["id"])
    pending = await scheduled_item(env, row["id"], dispatch=False)
    await finish_scheduled(env, second, "FAILED")
    current = await service.get(env.context, "automation_schedules", row["id"])
    await service.fire(current, utcnow())
    assert (await service.get(env.context, "automation_schedules", row["id"]))[
        "consecutive_failures"
    ] == 0
    await finish_scheduled(env, first, "FAILED")
    # 直接派发也须复核结果，不依赖某个 Worker 恰好先扫描计划。
    await asyncio.gather(*(service.dispatch(pending) for _ in range(2)))
    current = await service.get(env.context, "automation_schedules", row["id"])
    assert current["state"] == "PAUSED" and current["consecutive_failures"] == 2
    assert (await service.get(env.context, "automation_items", pending["id"]))["run_id"] is None
    await service.toggle_schedule(
        env.context, row["id"], Toggle(revision=current["revision"], active=True)
    )
    await service.dispatch(pending)
    assert (await service.get(env.context, "automation_items", pending["id"]))[
        "state"
    ] == "CANCELLED"


async def test_schedule_admission_failure_and_lowered_threshold(runtime_env):
    env = runtime_env
    await admitted(env, purpose="production")
    service = env.runs.automation
    body = ScheduleCreate(
        name="受理失败", request=request(env), interval_seconds=60, failure_threshold=3
    )
    row = await service.create_schedule(env.context, body)
    item = await scheduled_item(env, row["id"], dispatch=False)
    async with transaction(
        env.engine, env.context.scope, keys(env.context, ("automation_items", item["id"]))
    ) as uow:
        item = await repo(env.context.scope, "automation_items").change(
            uow,
            item["id"],
            item["revision"],
            {"request": request(env).model_copy(update={"input": {}}).model_dump(mode="json")},
        )
    await service.dispatch(item)
    await service.sweep(env.context.scope.channel_id)
    current = await service.get(env.context, "automation_schedules", row["id"])
    assert current["consecutive_failures"] == 1 and current["state"] == "ACTIVE"
    edited = await service.update_schedule(
        env.context,
        row["id"],
        ScheduleUpdate(
            **body.model_copy(update={"failure_threshold": 1}).model_dump(),
            revision=current["revision"],
        ),
    )
    assert edited["state"] == "PAUSED" and edited["consecutive_failures"] == 1
