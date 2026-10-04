"""RUN-A01/A02/A03/A05/A06/A07/A08/A09/A11 并发与故障窗口验收。"""

import asyncio
from datetime import timedelta

import pytest
from pydantic import ValidationError

from creativity_service.core.context import Scope, TaskEnvelope, current_context
from creativity_service.core.contracts import BusinessResult, UsageEvent
from creativity_service.core.database import assert_external_io_allowed, transaction
from creativity_service.core.deletion import ContentRef, DeletionService, RecoveryService
from creativity_service.core.primitives import RunInput, ServiceError, utcnow
from creativity_service.modules.runs.repositories import rows, save
from creativity_service.modules.usage import repositories as usage_repo
from creativity_service.modules.usage.schemas import AttemptPlan
from creativity_service.workers.dispatcher import Dispatcher
from creativity_service.workers.executor import execute_message
from creativity_service.workers.recovery import Recovery

pytestmark = pytest.mark.integration


def envelope(env, run_id):
    return TaskEnvelope(channel_id=env.context.scope.channel_id, run_id=run_id)


def result(value=1):
    return BusinessResult(
        schema_version="1",
        business_status="COMPLETED",
        data={"value": value},
        warnings=(),
        evidence_refs=(),
    )


async def values(env, name, **filters):
    async with env.engine.connect() as connection:
        return await rows(connection, name, env.context.scope.channel_id, **filters)


async def update_run(env, run_id, name, record_id, **updates):
    row = await env.runs.load(envelope(env, run_id))
    context = env.runs.context(row)
    async with transaction(
        env.engine, context.scope, env.runs.keys(context, run_id, row["conversation_id"])
    ) as uow:
        return await save(uow, name, record_id, updates)


async def expire_lease(env, run_id):
    lease = (await values(env, "run_leases", run_id=run_id))[0]
    await update_run(
        env, run_id, "run_leases", lease["id"], expires_at=utcnow() - timedelta(seconds=1)
    )


class Publisher:
    def __init__(self):
        self.messages = []
        self.fail = False

    async def publish(self, message):
        assert_external_io_allowed()
        if self.fail:
            raise OSError("内部连接信息不得泄露")
        self.messages.append(message)


async def test_concurrent_first_admission_and_key_rotation(env):
    context = env.context.model_copy(
        update={
            "principal_type": "service",
            "actor_id": None,
            "principal_id": "client_one",
            "client_id": "client_one",
            "key_id": "old_key",
        }
    )
    receipts = await asyncio.gather(
        *(env.runs.admit_run(context, env.request, "same") for _ in range(8))
    )
    assert len({r.run_id for r in receipts}) == 1
    run_id = receipts[0].run_id
    assert len(await values(env, "runs")) == len(await values(env, "dispatch_outbox")) == 1
    rotated = context.model_copy(update={"key_id": "rotated_key"})
    env.runs.resolver = None
    replay = await env.runs.admit_run(
        rotated, env.request.model_copy(update={"delivery": "sync"}), "same"
    )
    assert replay.run_id == run_id
    with pytest.raises(ServiceError, match="内容不同"):
        await env.runs.admit_run(
            rotated, RunInput(agent_code="test_agent", input={"value": 2}), "same"
        )
    env.authorization.denied = True
    with pytest.raises(ServiceError, match="撤销"):
        await env.runs.admit_run(rotated, env.request, "same")


async def test_admission_commit_without_publish_and_queue_failure(env):
    def crash(point):
        if point == "admission_committed_before_publish":
            raise RuntimeError("受理提交后退出")

    env.runs.fault = crash
    with pytest.raises(RuntimeError):
        await env.runs.admit_run(env.context, env.request, "lost")
    env.runs.fault = lambda _: None
    receipt = await env.runs.admit_run(env.context, env.request, "lost")
    publisher = Publisher()
    publisher.fail = True
    dispatcher = Dispatcher(env.runs, publisher)
    assert not await dispatcher.dispatch(envelope(env, receipt.run_id))
    assert (await env.runs.get_run(env.context, receipt.run_id)).state == "QUEUED"
    outbox = (await values(env, "dispatch_outbox"))[0]
    assert outbox["last_error"] == "BROKER_UNAVAILABLE"
    await update_run(env, receipt.run_id, "dispatch_outbox", outbox["id"], next_attempt_at=utcnow())
    publisher.fail = False
    assert await dispatcher.scan(env.context.scope.channel_id) == 1
    assert len(publisher.messages) == 1


async def test_published_unconfirmed_and_dual_workers(env):
    receipt = await env.runs.admit_run(env.context, env.request, "dispatch")
    publisher = Publisher()
    dispatcher = Dispatcher(env.runs, publisher)

    def crash(point):
        if point == "published_before_confirmation":
            raise RuntimeError("投递后退出")

    env.runs.fault = crash
    with pytest.raises(RuntimeError):
        await dispatcher.dispatch(envelope(env, receipt.run_id))
    env.runs.fault = lambda _: None
    outbox = (await values(env, "dispatch_outbox"))[0]
    await update_run(env, receipt.run_id, "dispatch_outbox", outbox["id"], next_attempt_at=utcnow())
    await dispatcher.dispatch(envelope(env, receipt.run_id))
    assert len(publisher.messages) == 2
    leases = await asyncio.gather(
        *(
            env.runs.claim_lease(envelope(env, receipt.run_id), worker)
            for worker in ("worker_a", "worker_b")
        )
    )
    assert sum(lease is not None for lease in leases) == 1
    assert (await env.runs.get_run(env.context, receipt.run_id)).state == "RUNNING"


@pytest.mark.parametrize("cancel_first", [True, False])
async def test_cancellation_wins_first_valid_transaction(env, cancel_first):
    receipt = await env.runs.admit_run(env.context, env.request, "cancel")
    lease = await env.runs.claim_lease(envelope(env, receipt.run_id), "worker")
    if cancel_first:
        assert (await env.runs.cancel(env.context, receipt.run_id)).state == "CANCEL_REQUESTED"
        assert await env.runs.start_step(lease, "compute") is None
        assert await env.runs.finish_run(lease, "SUCCEEDED", result()) == "CANCELLED"
    else:
        assert await env.runs.finish_run(lease, "SUCCEEDED", result()) == "SUCCEEDED"
        assert (await env.runs.cancel(env.context, receipt.run_id)).state == "SUCCEEDED"
    states = [e.event_type for e in await env.runs.events(env.context, receipt.run_id)]
    assert states.count("completed") == 1
    assert states.count("result") == (0 if cancel_first else 1)


async def test_checkpoint_dedup_recovery_and_stale_lease(env):
    receipt = await env.runs.admit_run(env.context, env.request, "checkpoint")
    old = await env.runs.claim_lease(envelope(env, receipt.run_id), "old")
    await env.runs.start_step(old, "compute")
    await env.runs.commit_step(
        old, "compute", {"value": 2}, checkpoint_key="one", checkpoint={"at": 1}
    )
    await env.runs.commit_step(
        old, "compute", {"value": 2}, checkpoint_key="one", checkpoint={"at": 1}
    )
    await expire_lease(env, receipt.run_id)
    lease = await env.runs.claim_lease(envelope(env, receipt.run_id), "new")
    assert lease.lease_version == old.lease_version + 1
    assert (await env.runs.start_step(lease, "compute"))["state"] == "SUCCEEDED"
    with pytest.raises(ServiceError, match="租约已失效"):
        await env.runs.finish_run(old, "SUCCEEDED", result())
    assert len(await values(env, "checkpoints")) == 1
    assert len(await values(env, "run_steps")) == 1
    assert len(await values(env, "run_recoveries")) == 1
    assert await env.runs.finish_run(lease, "SUCCEEDED", result(2)) == "SUCCEEDED"


async def test_model_return_then_crash_preserves_unknown_usage(env):
    receipt = await env.runs.admit_run(env.context, env.request, "model")
    lease = await env.runs.claim_lease(envelope(env, receipt.run_id), "old")
    await env.runs.start_step(lease, "model")
    plan = AttemptPlan(
        run_id=receipt.run_id,
        attempt_id="candidate",
        agent_id="agent_one",
        model_id="model_one",
        connection_id="connection_one",
        purpose="production",
        input_tokens=20,
        max_output_tokens=50,
    )
    attempt = await env.runs.start_attempt(lease, "model", plan)
    assert await env.runs.mark_sent(lease, attempt["id"])
    # 模型已返回但进程未写入返回值；只能依据已提交的发送意图恢复。
    await expire_lease(env, receipt.run_id)
    assert await env.runs.claim_lease(envelope(env, receipt.run_id), "new") is None
    assert (await env.runs.get_run(env.context, receipt.run_id)).state == "FAILED"
    old = (await values(env, "attempts"))[0]
    assert old["state"] == "UNKNOWN" and not old["retryable"]
    async with env.engine.connect() as connection:
        usage = await usage_repo.required(
            connection, "usage_records", env.context.scope.channel_id, attempt_id=attempt["id"]
        )
    assert (
        usage["state"] == "PENDING"
        and usage["amount"] is None
        and usage["usage_status"] == "MISSING"
    )
    await env.ledger.settle(
        UsageEvent(
            scope=env.context.scope,
            attempt_id=attempt["id"],
            connection_id="connection_one",
            source_request_id="remote_one",
            event_version=1,
            status="REPORTED",
            raw_usage={"input": 12, "output": 8},
            normalized_tokens={"input": 12, "output": 8},
            subset_relations={},
            cumulative=True,
            final=True,
            observed_at=utcnow(),
        )
    )
    assert (await env.runs.get_run(env.context, receipt.run_id)).state == "FAILED"


async def test_terminal_commit_before_release_converges(env):
    receipt = await env.runs.admit_run(env.context, env.request, "release")
    lease = await env.runs.claim_lease(envelope(env, receipt.run_id), "worker")

    def crash(point):
        if point == "terminal_committed_before_release":
            raise RuntimeError("终态已提交")

    env.runs.fault = crash
    with pytest.raises(RuntimeError):
        await env.runs.finish_run(lease, "SUCCEEDED", result())
    assert (await env.runs.load(envelope(env, receipt.run_id)))["resources_released"] is False
    env.runs.fault = lambda _: None
    await Recovery(env.runs).scan(env.context.scope.channel_id)
    await Recovery(env.runs).scan(env.context.scope.channel_id)
    assert (await env.runs.load(envelope(env, receipt.run_id)))["resources_released"] is True
    async with env.engine.connect() as connection:
        admission = await usage_repo.required(
            connection, "admissions", env.context.scope.channel_id, run_id=receipt.run_id
        )
    assert admission["status"] == "RELEASED"


@pytest.mark.parametrize("block", ["deadline", "authorization", "deletion", "cancel"])
async def test_recovery_cannot_restart_blocked_run(env, block):
    receipt = await env.runs.admit_run(env.context, env.request, "blocked")
    lease = await env.runs.claim_lease(envelope(env, receipt.run_id), "worker")
    await env.runs.start_step(lease, "compute")
    await env.runs.commit_step(
        lease, "compute", {"value": 1}, checkpoint_key="saved", checkpoint={}
    )
    await expire_lease(env, receipt.run_id)
    if block == "deadline":
        await update_run(
            env, receipt.run_id, "runs", receipt.run_id, deadline=utcnow() - timedelta(seconds=1)
        )
    elif block == "authorization":
        env.authorization.denied = True
    elif block == "deletion":
        await DeletionService(env.engine, env.authorization).mark(
            env.context, ContentRef("run", receipt.run_id), "测试删除"
        )
    else:
        await env.runs.cancel(env.context, receipt.run_id)
    assert await env.runs.claim_lease(envelope(env, receipt.run_id), "new") is None
    row = await env.runs.load(envelope(env, receipt.run_id))
    assert (
        row["state"]
        == {
            "deadline": "TIMED_OUT",
            "authorization": "FAILED",
            "deletion": "FAILED",
            "cancel": "CANCELLED",
        }[block]
    )


async def test_queued_deadline_rerun_and_output_validation(env):
    receipt = await env.runs.admit_run(env.context, env.request, "expired")
    await update_run(
        env, receipt.run_id, "runs", receipt.run_id, deadline=utcnow() - timedelta(seconds=1)
    )
    assert await env.runs.claim_lease(envelope(env, receipt.run_id), "worker") is None
    rerun = await env.runs.rerun(env.context, receipt.run_id, "rerun")
    assert rerun.run_id != receipt.run_id
    row = await env.runs.load(envelope(env, rerun.run_id))
    assert row["parent_run_id"] == receipt.run_id and row["timeout_seconds"] == 60
    lease = await env.runs.claim_lease(envelope(env, rerun.run_id), "worker")
    invalid = result().model_copy(update={"data": {"value": "invalid"}})
    with pytest.raises(ServiceError, match="输出结构"):
        await env.runs.finish_run(lease, "SUCCEEDED", invalid)
    assert (await env.runs.get_run(env.context, rerun.run_id)).state == "RUNNING"


async def test_finite_retries_and_event_sequence_under_concurrency(env):
    receipt = await env.runs.admit_run(env.context, env.request, "retry")
    lease = await env.runs.claim_lease(envelope(env, receipt.run_id), "worker")
    await env.runs.start_step(lease, "tool")
    for _ in range(3):
        attempt = await env.runs.start_attempt(lease, "tool")
        await env.runs.mark_sent(lease, attempt["id"])
        await env.runs.finish_attempt(lease, attempt["id"], "FAILED", retryable=True)
    with pytest.raises(ServiceError, match="重试次数"):
        await env.runs.start_attempt(lease, "tool")
    assert len(await values(env, "attempts")) == 3
    await asyncio.gather(
        *(env.runs.append_event(lease, "text_delta", {"text": str(i)}) for i in range(10))
    )
    events = await env.runs.events(env.context, receipt.run_id)
    assert [e.sequence for e in events] == list(range(1, len(events) + 1))
    second = await env.runs.events(
        env.context, receipt.run_id, after_sequence=events[2].sequence, limit=2
    )
    assert [e.sequence for e in second] == [4, 5]


async def test_channel_context_is_cleared_and_message_is_only_locator(env):
    receipt = await env.runs.admit_run(env.context, env.request, "context")
    seen = []

    class Executor:
        async def execute(self, context, lease):
            assert_external_io_allowed()
            assert current_context.get() == context
            seen.append(context.scope.channel_id)
            await env.runs.finish_run(lease, "SUCCEEDED", result())

    current_context.set(env.context)
    await execute_message(env.runs, envelope(env, receipt.run_id), "worker", Executor())
    assert current_context.get() is None
    await execute_message(env.runs, envelope(env, receipt.run_id), "worker", Executor())
    assert len(seen) == 1
    with pytest.raises(ServiceError, match="不存在"):
        await env.runs.claim_lease(
            TaskEnvelope(channel_id="other_channel", run_id=receipt.run_id), "worker"
        )
    with pytest.raises(ValidationError):
        TaskEnvelope.model_validate({"run_id": receipt.run_id})
    context2 = env.context.model_copy(
        update={"scope": Scope(channel_id="second_channel", environment="test")}
    )
    await RecoveryService(env.engine, env.authorization).initialize_fresh(context2)
    with pytest.raises(ServiceError, match="不存在"):
        await env.runs.get_run(context2, receipt.run_id)


class Turns:
    def __init__(self):
        self.fail = False
        self.finished = 0

    def keys(self, context, conversation_id):
        return []

    async def admit(self, uow, context, run, request):
        # 测试消息使用同事务内容表，不创建会话模块的生产表。
        await save(
            uow,
            "run_contents",
            "message_" + run["id"],
            {"run_id": run["id"], "kind": "test_message", "payload": request.input},
        )
        if self.fail:
            raise ServiceError("TURN_REJECTED", "测试轮次拒绝", 409)

    async def finish(self, uow, context, run):
        self.finished += 1


async def test_session_budget_and_message_atomicity(env):
    from decimal import Decimal

    from creativity_service.core.primitives import new_id

    turns = Turns()
    env.runs.turns = turns
    request = env.request.model_copy(update={"conversation_id": "conversation_one"})
    turns.fail = True
    with pytest.raises(ServiceError, match="轮次拒绝"):
        await env.runs.admit_run(env.context, request, "reject")
    assert not await values(env, "runs")
    assert not await values(env, "run_contents")
    assert not await values(env, "run_occupancies")
    async with env.engine.connect() as connection:
        assert not await usage_repo.rows(connection, "admissions", env.context.scope.channel_id)
    turns.fail = False
    results = await asyncio.gather(
        *(env.runs.admit_run(env.context, request, f"msg_{i}") for i in range(4)),
        return_exceptions=True,
    )
    accepted = [r for r in results if not isinstance(r, Exception)]
    assert len(accepted) == 1
    assert all(r.code == "SESSION_BUSY" for r in results if isinstance(r, Exception))
    assert len(await values(env, "run_contents", kind="test_message")) == 1
    await env.runs.cancel(env.context, accepted[0].run_id)
    await env.runs.cancel(env.context, accepted[0].run_id)
    assert turns.finished == 1
    async with transaction(
        env.engine,
        env.context.scope,
        [
            *env.budgets.admission_keys(env.context, "unused"),
            usage_repo.budget_configuration_key(env.context.scope.channel_id),
        ],
    ) as uow:
        await usage_repo.save(
            uow,
            "budget_policies",
            new_id("budget"),
            {
                "scope_type": "channel",
                "scope_id": env.context.scope.channel_id,
                "period": "month",
                "timezone": "Asia/Shanghai",
                "currency": None,
                "limit_value": Decimal(0),
                "unit": "requests",
                "mode": "HARD",
                "thresholds": ["0.8", "1"],
                "status": "ACTIVE",
                "name": "拒绝新请求",
                "version_id": "version_test",
            },
        )
    with pytest.raises(ServiceError, match="上限"):
        await env.runs.admit_run(env.context, request, "budget_reject")
    assert len(await values(env, "runs")) == 1
    assert len(await values(env, "run_contents", kind="test_message")) == 1
    assert not await values(env, "run_occupancies", state="HELD")
    assert len(await values(env, "dispatch_outbox")) == 1


async def test_concurrent_success_and_cancel_never_reverse_terminal(env):
    for i in range(4):
        receipt = await env.runs.admit_run(env.context, env.request, f"race_{i}")
        lease = await env.runs.claim_lease(envelope(env, receipt.run_id), "worker")
        await asyncio.gather(
            env.runs.finish_run(lease, "SUCCEEDED", result()),
            env.runs.cancel(env.context, receipt.run_id),
        )
        row = await env.runs.get_run(env.context, receipt.run_id)
        assert row.state in {"SUCCEEDED", "CANCELLED"}
        assert await env.runs.finish_run(lease, "SUCCEEDED", result(99)) == row.state


async def test_worker_two_channels_same_agent_and_late_original_usage(env):
    from uuid import uuid4

    from creativity_service.core.versioning import VersionService
    from creativity_service.modules.runs.schemas import (
        ExecutionPolicy,
        ResolvedDefinition,
        StepPolicy,
    )
    from tests.integration.core.conftest import TestVersionValidator
    from tests.integration.runs.conftest import InternalDefinition

    receipt1 = await env.runs.admit_run(env.context, env.request, "same")
    context2 = env.context.model_copy(
        update={"scope": Scope(channel_id=f"channel_{uuid4().hex}", environment="test")}
    )
    await RecoveryService(env.engine, env.authorization).initialize_fresh(context2)
    versions = VersionService(env.engine, env.authorization, TestVersionValidator())
    agent = await versions.create_draft(
        context2, "agent", "agent_one", "同名任务", {}, [], {"type": "object"}
    )
    agent = await versions.freeze(context2, agent.version_id, 1)
    env.runs.resolver = InternalDefinition(
        ResolvedDefinition(
            agent_id="agent_one",
            agent_name="同名任务",
            version_ids=(agent.version_id,),
            input_schema={"type": "object"},
            output_schema={"type": "object"},
            policy=ExecutionPolicy(
                steps=(
                    StepPolicy(
                        node_key="compute", kind="compute", target_version_id=agent.version_id
                    ),
                )
            ),
        )
    )
    receipt2 = await env.runs.admit_run(context2, env.request, "same")
    seen = []

    class Executor:
        async def execute(self, context, lease):
            seen.append(context.scope.channel_id)
            assert current_context.get().scope == context.scope
            await env.runs.start_step(lease, "compute")
            await env.runs.commit_step(
                lease, "compute", {"value": 1}, checkpoint_key="same", checkpoint={}
            )
            await env.runs.finish_run(lease, "SUCCEEDED", result())

    for context, receipt in ((env.context, receipt1), (context2, receipt2)):
        message = TaskEnvelope(channel_id=context.scope.channel_id, run_id=receipt.run_id)
        await execute_message(env.runs, message, "same_worker", Executor())
        await execute_message(env.runs, message, "same_worker", Executor())
        assert current_context.get() is None
    assert seen == [env.context.scope.channel_id, context2.scope.channel_id]
    async with env.engine.connect() as connection:
        for context, receipt in ((env.context, receipt1), (context2, receipt2)):
            checkpoints = await rows(connection, "checkpoints", context.scope.channel_id)
            assert len(checkpoints) == 1 and checkpoints[0]["run_id"] == receipt.run_id


async def test_saved_attempt_output_survives_lease_loss_without_new_call(env):
    receipt = await env.runs.admit_run(env.context, env.request, "saved_attempt")
    lease = await env.runs.claim_lease(envelope(env, receipt.run_id), "old")
    await env.runs.start_step(lease, "model")
    attempt = await env.runs.start_attempt(
        lease,
        "model",
        AttemptPlan(
            run_id=receipt.run_id,
            attempt_id="candidate",
            agent_id="agent_one",
            model_id="model_one",
            connection_id="connection_one",
            purpose="production",
            input_tokens=20,
            max_output_tokens=50,
        ),
    )
    await env.runs.mark_sent(lease, attempt["id"])
    await env.runs.finish_attempt(
        lease, attempt["id"], "SUCCEEDED", source_request_id="remote_saved", output={"value": 3}
    )
    await expire_lease(env, receipt.run_id)
    resumed = await env.runs.claim_lease(envelope(env, receipt.run_id), "new")
    progress = await env.runs.load_progress(resumed, "model")
    assert progress["output"] == {"value": 3}
    await env.runs.commit_step(
        resumed, "model", progress["output"], checkpoint_key="saved", checkpoint={"value": 3}
    )
    with pytest.raises(ServiceError, match="恢复点内容不同"):
        await env.runs.commit_step(
            resumed, "model", progress["output"], checkpoint_key="saved", checkpoint={"value": 4}
        )
    assert len(await values(env, "attempts")) == 1
    assert await env.runs.finish_run(resumed, "SUCCEEDED", result(3)) == "SUCCEEDED"


async def test_management_identity_idempotency_and_unfinished_retention(env):
    first = await env.runs.admit_run(env.context, env.request, "retained")
    idempotency = (await values(env, "run_idempotency"))[0]
    from creativity_service.modules.runs.repositories import idempotency_key

    async with transaction(
        env.engine,
        env.context.scope,
        [
            *env.runs.keys(env.context, first.run_id),
            idempotency_key(env.context.scope, idempotency["scope_digest"], idempotency["key"]),
        ],
    ) as uow:
        await save(
            uow, "run_idempotency", idempotency["id"], {"expires_at": utcnow() - timedelta(days=1)}
        )
    assert (await env.runs.admit_run(env.context, env.request, "retained")).run_id == first.run_id
    other = env.context.model_copy(
        update={"actor_id": "other_operator", "principal_id": "other_operator"}
    )
    second = await env.runs.admit_run(other, env.request, "retained")
    assert second.run_id != first.run_id
    events = await env.runs.events(env.context, first.run_id)
    await update_run(
        env,
        first.run_id,
        "run_events",
        events[0].event_id,
        expires_at=utcnow() - timedelta(seconds=1),
    )
    with pytest.raises(ServiceError, match="事件已过期"):
        await env.runs.events(env.context, first.run_id)
    assert (await env.runs.get_run(env.context, first.run_id)).state == "QUEUED"


async def test_routes_require_server_definition_and_offer_queries(env):
    import httpx
    from fastapi import FastAPI

    from creativity_service.api.errors import register_error_handlers
    from creativity_service.core.context import require_http_context
    from creativity_service.modules.runs.api import admin_router, router

    app = FastAPI()
    app.state.runs = env.runs
    app.include_router(router, prefix="/api/v1")
    app.include_router(admin_router, prefix="/admin/v1")
    register_error_handlers(app)
    app.dependency_overrides[require_http_context] = lambda: env.context
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        env.runs.resolver = None
        closed = await client.post(
            "/api/v1/runs", headers={"Idempotency-Key": "api"}, json=env.request.model_dump()
        )
        assert closed.status_code == 503
        injected = await client.post(
            "/api/v1/runs",
            headers={"Idempotency-Key": "api"},
            json={**env.request.model_dump(), "execution_definition": {"steps": []}},
        )
        assert injected.status_code == 422
        env.runs.resolver = env.resolver
        accepted = await client.post(
            "/api/v1/runs", headers={"Idempotency-Key": "api"}, json=env.request.model_dump()
        )
        assert accepted.status_code == 202
        run_id = accepted.json()["run_id"]
        assert (await client.get(f"/api/v1/runs/{run_id}")).json()["state_label"] == "排队中"
        assert (await client.post(f"/api/v1/runs/{run_id}/cancel")).json()["state"] == "CANCELLED"
        again = await client.post(
            f"/admin/v1/runs/{run_id}/rerun", headers={"Idempotency-Key": "rerun_api"}, json={}
        )
        assert again.status_code == 202 and again.json()["run_id"] != run_id


async def test_management_subject_scope_is_derived_from_stored_run(env):
    scope = Scope(
        channel_id=env.context.scope.channel_id,
        environment="test",
        data_scope_id="domain_one",
        subject_type="customer",
        subject_id="customer_one",
    )
    subject_context = env.context.model_copy(update={"scope": scope})
    await RecoveryService(env.engine, env.authorization).initialize_fresh(subject_context)
    receipt = await env.runs.admit_run(subject_context, env.request, "subject_run")
    manager = env.context.model_copy(
        update={"scope": scope.model_copy(update={"subject_type": None, "subject_id": None})}
    )
    assert (await env.runs.get_run(manager, receipt.run_id)).state == "QUEUED"
    page = await env.runs.list_runs(manager, subject_type="customer", subject_id="customer_one")
    assert [r["run_id"] for r in page["items"]] == [receipt.run_id]
    foreign = manager.model_copy(
        update={"scope": manager.scope.model_copy(update={"data_scope_id": "domain_other"})}
    )
    with pytest.raises(ServiceError, match="不存在"):
        await env.runs.get_run(foreign, receipt.run_id)
