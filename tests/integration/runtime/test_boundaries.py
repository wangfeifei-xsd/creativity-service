"""真实运行服务上的故障、流式授权和限额验收，供应商响应为夹具。"""

import asyncio
from datetime import timedelta

import pytest
from fastapi import Request
from redis.exceptions import ConnectionError as RedisConnectionError

from creativity_service.core.context import TaskEnvelope
from creativity_service.core.database import transaction
from creativity_service.core.deletion import ContentRef, DeletionService
from creativity_service.core.primitives import ServiceError, utcnow
from creativity_service.integrations.models.contracts import ModelEvent
from creativity_service.modules.models.schemas import RetryPolicy, RouteInput, RouteVersionInput
from creativity_service.modules.runs.repositories import rows, save
from creativity_service.modules.runtime.stream import event_stream
from creativity_service.workers.executor import execute_message
from tests.integration.runtime.test_execution import admitted, pytestmark
from tests.support.resources import publish_resource

__all__ = ["pytestmark"]


async def route_definition(env, *, streaming=False, calls=6):
    route = await env.models.routing.create(
        env.tenant.manager, RouteInput(code="retry", name="重试路由")
    )
    version = await env.models.routing.create_version(
        env.tenant.manager,
        route.id,
        RouteVersionInput(
            label="重试初版",
            primary_model=env.model.id,
            required_capabilities=[
                "text",
                "structured_output",
                *(["streaming"] if streaming else []),
            ],
            retry_policy=RetryPolicy(max_attempts=3, retries_per_model=2),
        ),
    )
    await publish_resource(env, "model_route", route.id)
    return env.definition.model_copy(
        update={
            "bindings": env.definition.bindings.model_copy(
                update={"model_route_version": version.version_id}
            ),
            "limits": env.definition.limits.model_copy(update={"max_model_rounds": calls}),
        }
    )


async def expire_lease(env, run_id):
    async with transaction(
        env.engine, env.context.scope, env.runs.keys(env.context, run_id)
    ) as uow:
        lease = (
            await rows(uow.connection, "run_leases", env.context.scope.channel_id, run_id=run_id)
        )[0]
        await save(uow, "run_leases", lease["id"], {"expires_at": utcnow() - timedelta(seconds=1)})


async def test_retry_preserves_attempts_and_unknown_usage(runtime_env):
    env = runtime_env
    definition = await route_definition(env)
    receipt, message = await admitted(env, definition=definition)
    env.adapter.failures = ["MODEL_UNAVAILABLE"]
    await execute_message(env.runs, message, "worker", env.runtime)
    value = await env.runs.get_run(env.context, receipt.run_id)
    assert value.state == "SUCCEEDED", value.error
    assert len(env.adapter.calls) == 2
    assert value.usage_summary["attempt_count"] == 2
    assert value.usage_summary["pending_count"] == 1
    assert value.usage_summary["input_tokens"] is None
    assert value.usage_summary["complete"] is False


async def test_retry_counts_toward_model_limit(runtime_env):
    env = runtime_env
    receipt, message = await admitted(env, definition=await route_definition(env, calls=1))
    env.adapter.failures = ["MODEL_UNAVAILABLE"] * 3
    await execute_message(env.runs, message, "worker", env.runtime)
    value = await env.runs.get_run(env.context, receipt.run_id)
    assert value.state == "FAILED" and value.error.code == "CALL_LIMIT"
    assert len(env.adapter.calls) == 1


async def test_schema_repair_is_finite_and_metered(runtime_env):
    env = runtime_env
    receipt, message = await admitted(env)
    env.adapter.responses = [{"invalid": True}]
    await execute_message(env.runs, message, "worker", env.runtime)
    value = await env.runs.get_run(env.context, receipt.run_id)
    assert value.state == "SUCCEEDED", value.error
    assert len(env.adapter.calls) == 2
    assert value.usage_summary["input_tokens"] == 20
    rerun = await env.runs.rerun(env.context, receipt.run_id, "invalid_again")
    env.adapter.responses = [{"invalid": True}] * 4
    await execute_message(
        env.runs,
        TaskEnvelope(channel_id=env.context.scope.channel_id, run_id=rerun.run_id),
        "worker",
        env.runtime,
    )
    value = await env.runs.get_run(env.context, rerun.run_id)
    assert value.state == "FAILED" and value.error.code == "MODEL_OUTPUT_INVALID"
    assert len(env.adapter.calls) == 4
    row = await env.runs.load(
        TaskEnvelope(channel_id=env.context.scope.channel_id, run_id=rerun.run_id)
    )
    assert row["purpose"] == "debug" and row["parent_run_id"] == receipt.run_id


async def test_stream_failure_never_appends_fallback_text(runtime_env):
    env = runtime_env
    receipt, message = await admitted(env, definition=await route_definition(env, streaming=True))
    original = env.adapter.events

    async def fail_after_text(*args, **kwargs):
        async for event in original(*args, **kwargs):
            if event.kind == "completed":
                yield ModelEvent(
                    kind="failed",
                    attempt_id=event.attempt_id,
                    error_code="MODEL_UNAVAILABLE",
                    message="连接中断",
                    retryable=True,
                )
            else:
                yield event

    env.adapter.events = fail_after_text
    await execute_message(env.runs, message, "worker", env.runtime)
    value = await env.runs.get_run(env.context, receipt.run_id)
    assert value.state == "FAILED" and value.result is None
    assert value.partial_output["text"]
    assert value.usage_summary["complete"] is True
    assert len(env.adapter.calls) == 1


@pytest.mark.parametrize("failure", ["expiry", "redis"])
async def test_sse_auth_control_closes_but_task_survives(runtime_env, monkeypatch, failure):
    env = runtime_env
    receipt, message = await admitted(env)

    async def receive():
        await asyncio.Future()

    request = Request(
        {
            "type": "http",
            "path": f"/admin/v1/runs/{receipt.run_id}/events",
            "query_string": b"",
            "headers": [],
            "app": env.client._transport.app,
        },
        receive,
    )
    response = await event_stream(request, env.context, env.runs, receipt.run_id)
    iterator = response.body_iterator
    assert "event: accepted" in await anext(iterator)
    if failure == "expiry":
        await env.redis.expire(env.iam.authentication.tokens.token_key(env.context.token_digest), 0)
    else:

        async def unavailable(*args, **kwargs):
            raise RedisConnectionError("测试认证存储不可用")

        monkeypatch.setattr(env.iam.authentication.tokens.redis, "eval", unavailable)
    control = await anext(iterator)
    assert "event: control" in control
    assert '"status":401' in control if failure == "expiry" else '"status":503' in control
    with pytest.raises(StopAsyncIteration):
        await anext(iterator)
    monkeypatch.undo()
    await execute_message(env.runs, message, "worker", env.runtime)
    assert (await env.runs.load(message))["state"] == "SUCCEEDED"


async def test_sse_drains_events_committed_after_empty_poll(runtime_env, monkeypatch):
    env = runtime_env
    receipt, message = await admitted(env)
    original = env.runs.events
    finished = False

    async def finish_after_empty_poll(*args, **kwargs):
        nonlocal finished
        events = await original(*args, **kwargs)
        if not events and not finished:
            # 确定性模拟查事件后、读终态前提交结果，不能将旧空页当作最终进度。
            finished = True
            await execute_message(env.runs, message, "worker", env.runtime)
        return events

    monkeypatch.setattr(env.runs, "events", finish_after_empty_poll)
    response = await env.client.get(f"/admin/v1/runs/{receipt.run_id}/events")
    assert response.status_code == 200
    value = await env.runs.get_run(env.context, receipt.run_id)
    assert finished and value.state == "SUCCEEDED", value.error
    assert response.text.count("event: result\n") == 1, response.text
    assert response.text.count("event: completed\n") == 1, response.text
    identifiers = [int(line[4:]) for line in response.text.splitlines() if line.startswith("id: ")]
    persisted = await original(env.context, receipt.run_id)
    assert identifiers == [event.sequence for event in persisted]
    # 消费者已读到完成序号时仍应正常关闭，不能为补读而永远轮询。
    resumed = await env.client.get(
        f"/admin/v1/runs/{receipt.run_id}/events",
        headers={"Last-Event-ID": str(identifiers[-1])},
    )
    assert resumed.status_code == 200 and resumed.text == ""


async def test_unknown_sent_response_never_resends(runtime_env):
    env = runtime_env
    receipt, message = await admitted(env)

    async def crash(*args):
        raise RuntimeError("模拟发送后崩溃")

    env.adapter.before = crash
    with pytest.raises(RuntimeError):
        await execute_message(env.runs, message, "worker_a", env.runtime)
    env.adapter.before = None
    await expire_lease(env, receipt.run_id)
    await execute_message(env.runs, message, "worker_b", env.runtime)
    value = await env.runs.get_run(env.context, receipt.run_id)
    assert value.state == "FAILED" and value.error.code == "MODEL_RESULT_UNKNOWN"
    assert len(env.adapter.calls) == 1
    assert value.usage_summary["pending_count"] == 1


async def test_stateful_crash_recovers_saved_response_and_fences_old_lease(runtime_env):
    from creativity_service.integrations.checkpoints import SQLAlchemySaver

    env = runtime_env
    definition = env.definition.model_copy(
        update={"workflow_type": "stateful", "entrypoint": "stateful.v1"}
    )
    receipt, message = await admitted(env, definition=definition)
    original = env.runs.commit_step
    old_lease = None

    async def crash(lease, *args, **kwargs):
        nonlocal old_lease
        old_lease = lease
        raise RuntimeError("模拟步骤提交前崩溃")

    env.runs.commit_step = crash
    with pytest.raises(RuntimeError):
        await execute_message(env.runs, message, "worker_a", env.runtime)
    env.runs.commit_step = original
    await expire_lease(env, receipt.run_id)
    await execute_message(env.runs, message, "worker_b", env.runtime)
    value = await env.runs.get_run(env.context, receipt.run_id)
    assert value.state == "SUCCEEDED", value.error
    assert len(env.adapter.calls) == 1
    with pytest.raises(ServiceError):
        await SQLAlchemySaver(env.runs, old_lease).store("langgraph:", "stale", None, {})


async def test_deletion_drops_late_text_but_settles_usage(runtime_env):
    env = runtime_env
    receipt, message = await admitted(env, definition=await route_definition(env, streaming=True))

    async def remove(*args):
        await DeletionService(env.engine, env.iam.authorization).mark(
            env.context, ContentRef("run", receipt.run_id), "删除运行"
        )

    env.adapter.before = remove
    await execute_message(env.runs, message, "worker", env.runtime)
    row = await env.runs.load(message)
    assert row["state"] == "FAILED" and row["result_ref"] is None
    from creativity_service.modules.usage.repositories import rows as usage_rows

    async with env.engine.connect() as connection:
        usage = await usage_rows(
            connection, "usage_records", env.context.scope.channel_id, run_id=receipt.run_id
        )
    assert usage[0]["input_tokens"] == 10, row["error"]
    assert await env.runs.claim_lease(message, "worker_b") is None


async def test_cross_channel_message_cannot_execute(runtime_env):
    env = runtime_env
    receipt, message = await admitted(env)
    with pytest.raises(ServiceError):
        await execute_message(
            env.runs,
            message.model_copy(update={"channel_id": "another_channel"}),
            "wrong",
            env.runtime,
        )
    assert not env.adapter.calls
    await execute_message(env.runs, message, "right", env.runtime)
    assert (await env.runs.get_run(env.context, receipt.run_id)).state == "SUCCEEDED"


async def test_step_timeout_drains_captured_usage(runtime_env):
    env = runtime_env
    step = env.definition.steps[0].model_copy(update={"timeout_seconds": 5})
    receipt, message = await admitted(
        env, definition=env.definition.model_copy(update={"steps": (step,)})
    )
    original = env.adapter.events

    async def delayed(context, config, request, attempt, reservation, cancellation, **kwargs):
        async for event in original(
            context, config, request, attempt, reservation, cancellation, **kwargs
        ):
            if event.kind == "text":
                await cancellation.event.wait()
            yield event

    env.adapter.events = delayed
    await execute_message(env.runs, message, "worker", env.runtime)
    value = await env.runs.get_run(env.context, receipt.run_id)
    assert value.state == "FAILED" and value.error.code == "STEP_TIMEOUT"
    assert value.usage_summary["input_tokens"] == 10


async def test_deleted_checkpoint_cannot_be_recreated(runtime_env):
    from creativity_service.integrations.checkpoints import SQLAlchemySaver

    env = runtime_env
    receipt, message = await admitted(
        env,
        definition=env.definition.model_copy(
            update={"workflow_type": "stateful", "entrypoint": "stateful.v1"}
        ),
    )
    lease = await env.runs.claim_lease(message, "worker")
    saver = SQLAlchemySaver(env.runs, lease)
    await saver.store("langgraph:", "saved", None, {"private": "待删除内容"})
    await DeletionService(env.engine, env.iam.authorization).mark(
        env.context, ContentRef("run", receipt.run_id), "删除运行"
    )
    await env.cleanup.clean(env.context, ContentRef("run", receipt.run_id))
    with pytest.raises(ServiceError):
        await saver.store("langgraph:", "late", None, {"private": "迟到内容"})
    async with env.engine.connect() as connection:
        assert not await rows(
            connection, "checkpoints", env.context.scope.channel_id, run_id=receipt.run_id
        )
        assert not [
            r
            for r in await rows(
                connection, "run_contents", env.context.scope.channel_id, run_id=receipt.run_id
            )
            if r["kind"] == "checkpoint"
        ]
