"""RUN、AGT 与 IAM 边界在正式执行器中的组合验证，不算真实供应商验收。"""

import asyncio
from datetime import timedelta

import pytest

from creativity_service.core.context import TaskEnvelope
from creativity_service.core.database import transaction
from creativity_service.core.primitives import RunInput, utcnow
from creativity_service.modules.agents.schemas import AgentTestInput
from creativity_service.modules.runs.repositories import rows, save
from creativity_service.workers.executor import execute_message
from tests.integration.agents.test_agents import publish

pytestmark = [
    pytest.mark.integration,
    pytest.mark.parametrize(
        "agent_env",
        [
            {
                "environment": "test",
                "context_limit": None,
                "independent_actions": ["release:publish", "data:read_sensitive", "data:export"],
            }
        ],
        indirect=True,
    ),
]


async def admitted(env, *, definition=None, purpose="debug"):
    detail = await env.agents.create(
        env.context, env.body.model_copy(update={"definition": definition or env.definition})
    )
    version = detail.versions[0]
    if purpose == "production":
        await publish(env, detail)
        receipt = await env.runs.admit_run(
            env.context,
            RunInput(agent_code=env.body.agent_code, input={"request": "请处理"}),
            "production",
        )
    else:
        receipt = await env.agents.test(
            env.context,
            version.version_id,
            AgentTestInput(
                revision=version.revision, input={"request": "请处理"}, idempotency_key="debug"
            ),
        )
    return receipt, TaskEnvelope(channel_id=env.context.scope.channel_id, run_id=receipt.run_id)


async def test_structured_result_budget_and_duplicate_delivery(runtime_env):
    env = runtime_env
    receipt, message = await admitted(env)
    await asyncio.gather(
        *(execute_message(env.runs, message, f"worker_{i}", env.runtime) for i in range(2))
    )
    value = await env.runs.get_run(env.context, receipt.run_id)
    assert value.state == "SUCCEEDED", value.error
    assert value.result.data == {"answer": "验证完成"}
    assert len(env.adapter.calls) == 1
    assert env.adapter.calls[0][1].output_mode == "native"
    assert value.usage_summary["input_tokens"] == 10
    assert value.usage_summary["attempt_count"] == 1
    detail = await env.runs.detail(env.context, receipt.run_id)
    assert detail["purpose_label"] == "调试"
    async with env.engine.connect() as connection:
        assert not await rows(connection, "run_occupancies", env.context.scope.channel_id)


@pytest.mark.parametrize("invalid", [False, True])
async def test_text_only_route_runs_with_local_output_validation(runtime_env, invalid):
    from creativity_service.core.locking import record_key
    from creativity_service.modules.agents.repositories import repository
    from creativity_service.modules.models.schemas import RetryPolicy, RouteVersionInput

    env = runtime_env
    async with transaction(
        env.engine,
        env.context.scope,
        [
            record_key(env.context.scope.channel_id, "models", env.model.id),
        ],
    ) as uow:
        repo = repository("models", env.context.scope)
        model = await repo.get(uow.connection, env.model.id)
        await repo.change(
            uow,
            env.model.id,
            model["revision"],
            {
                "capabilities": {
                    **model["capabilities"],
                    "structured_output": {"state": "UNSUPPORTED"},
                }
            },
        )
        route = await repository("resource_versions", env.context.scope).get(
            uow.connection, env.route_id
        )
    await env.models.routing.create_version(
        env.tenant.manager,
        env.route_id,
        RouteVersionInput(
            revision=route["revision"],
            primary_model=env.model.id,
            required_capabilities=["text"],
            retry_policy=RetryPolicy(max_attempts=3, retries_per_model=2),
        ),
    )
    receipt, message = await admitted(env, purpose="production")
    if invalid:
        env.adapter.responses = [{"invalid": True}] * 3
    await execute_message(env.runs, message, "worker", env.runtime)
    result = await env.runs.get_run(env.context, receipt.run_id)
    if invalid:
        assert result.state == "FAILED" and result.error.code == "MODEL_OUTPUT_INVALID"
        assert result.result is None and len(env.adapter.calls) == 2
    else:
        assert result.state == "SUCCEEDED", result.error
        assert result.result.data == {"answer": "验证完成"}
    for _, request, _ in env.adapter.calls:
        assert request.output_mode == "prompt" and not request.requires_native_output
        assert request.output_schema == env.definition.steps[0].output_schema
        assert '"business_status"' in request.messages[0]["content"]


async def test_stateful_checkpoint_persists_and_lease_blocks_stale_writes(runtime_env):
    env = runtime_env
    definition = env.definition.model_copy(
        update={"workflow_type": "stateful", "entrypoint": "stateful.v1"}
    )
    receipt, message = await admitted(env, definition=definition)
    await execute_message(env.runs, message, "worker_a", env.runtime)
    result = await env.runs.get_run(env.context, receipt.run_id)
    assert result.state == "SUCCEEDED", result.model_dump()
    async with env.engine.connect() as connection:
        points = await rows(
            connection, "checkpoints", env.context.scope.channel_id, run_id=receipt.run_id
        )
    assert any(p["namespace"].startswith("langgraph:") for p in points)
    await execute_message(env.runs, message, "worker_b", env.runtime)
    assert len(env.adapter.calls) == 1


async def test_sync_timeout_reuses_original_run_and_sse_replays(runtime_env):
    env = runtime_env
    receipt, message = await admitted(env, purpose="production")
    response = await env.client.post("/api/v1/runs", json={})
    assert response.status_code == 401
    # 管理端调用传输函数，正文与业务端复用同一受理入口。
    from fastapi import Request, Response

    from creativity_service.modules.runs.api import admit

    request = Request({"type": "http", "app": env.client._transport.app})
    result = await admit(
        RunInput(agent_code=env.body.agent_code, input={"request": "请处理"}, delivery="sync"),
        "production",
        env.context,
        env.runs,
        request,
        Response(status_code=202),
    )
    assert result.run_id == receipt.run_id
    await execute_message(env.runs, message, "worker", env.runtime)
    response = await env.client.get(f"/admin/v1/runs/{receipt.run_id}/events")
    assert response.status_code == 200
    assert response.text.count("event: result\n") == 1
    identifiers = [int(line[4:]) for line in response.text.splitlines() if line.startswith("id: ")]
    assert identifiers == sorted(set(identifiers))
    response = await env.client.get(
        f"/admin/v1/runs/{receipt.run_id}/events", headers={"Last-Event-ID": str(identifiers[-2])}
    )
    assert response.text.count("event: completed\n") == 1
    assert "event: result" not in response.text
    async with transaction(
        env.engine, env.context.scope, env.runs.keys(env.context, receipt.run_id)
    ) as uow:
        for event in await rows(
            uow.connection, "run_events", env.context.scope.channel_id, run_id=receipt.run_id
        ):
            await save(
                uow, "run_events", event["id"], {"expires_at": utcnow() - timedelta(hours=25)}
            )
    expired = await env.client.get(f"/admin/v1/runs/{receipt.run_id}/events")
    assert expired.status_code == 410
    assert (await env.client.get(f"/admin/v1/runs/{receipt.run_id}")).status_code == 200


async def test_cancel_inflight_preserves_usage_without_success(runtime_env):
    env = runtime_env
    receipt, message = await admitted(env)

    async def cancel(context, attempt):
        await env.runs.cancel(env.context, receipt.run_id)

    env.adapter.before = cancel
    await execute_message(env.runs, message, "worker", env.runtime)
    value = await env.runs.get_run(env.context, receipt.run_id)
    assert value.state == "CANCELLED"
    assert value.result is None
    assert value.usage_summary["attempt_count"] == 1
    assert value.usage_summary["input_tokens"] == 10


async def test_model_validation_without_published_agent_creates_debug_run(runtime_env):
    env = runtime_env
    from creativity_service.modules.models.schemas import TestInput as Cases

    test = await env.models.testing.create(
        env.tenant.manager, env.model.id, Cases(cases=["text", "usage"])
    )
    assert test.run_id, test.model_dump_json()
    await execute_message(
        env.runs,
        TaskEnvelope(channel_id=env.context.scope.channel_id, run_id=test.run_id),
        "worker",
        env.runtime,
    )
    value = await env.runs.get_run(env.context, test.run_id)
    assert value.state == "SUCCEEDED", value.error
    assert value.usage_summary["attempt_count"] == 2
    assert value.result.data["evidence"] == "fixture"


async def test_saved_response_recovery_does_not_resend(runtime_env):
    env = runtime_env
    receipt, message = await admitted(env)
    original = env.runs.commit_step

    async def crash(*args, **kwargs):
        raise RuntimeError("模拟模型响应已保存后的进程崩溃")

    env.runs.commit_step = crash
    with pytest.raises(RuntimeError):
        await execute_message(env.runs, message, "worker_a", env.runtime)
    env.runs.commit_step = original
    async with transaction(
        env.engine, env.context.scope, env.runs.keys(env.context, receipt.run_id)
    ) as uow:
        lease = (
            await rows(
                uow.connection, "run_leases", env.context.scope.channel_id, run_id=receipt.run_id
            )
        )[0]
        await save(uow, "run_leases", lease["id"], {"expires_at": utcnow() - timedelta(seconds=1)})
    await execute_message(env.runs, message, "worker_b", env.runtime)
    recovered = await env.runs.get_run(env.context, receipt.run_id)
    assert recovered.state == "SUCCEEDED", recovered.error
    assert len(env.adapter.calls) == 1
