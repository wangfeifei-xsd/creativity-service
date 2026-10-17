"""真实运行、用量和恢复链路验证结构修复；供应商正文由协议夹具控制。"""

import json
from dataclasses import replace

import httpx
import pytest

from creativity_service.core.context import TaskEnvelope
from creativity_service.modules.agents.assistance_schemas import AssistanceRequest
from creativity_service.modules.models.repositories import repository
from creativity_service.modules.models.schemas import RouteVersionInput
from creativity_service.modules.runs.repositories import rows
from creativity_service.workers.executor import execute_message
from tests.integration.runtime.test_agent_assistance import output, proposal
from tests.integration.runtime.test_boundaries import expire_lease
from tests.integration.runtime.test_execution import admitted
from tests.models.test_protocols import adapter as protocol_adapter

pytestmark = pytest.mark.integration


async def submit_assistance(env, invalid, *, always_invalid=False):
    async with env.engine.connect() as connection:
        route = await repository(env.context.scope, "resource_versions").get(
            connection, env.route_id
        )
    await env.models.routing.create_version(
        env.tenant.manager,
        env.route_id,
        RouteVersionInput(
            revision=route["revision"],
            primary_model=env.model.id,
            required_capabilities=["text"],
        ),
    )
    calls = []

    async def handler(request):
        body = json.loads(request.content)
        calls.append(body)
        return httpx.Response(
            200,
            json={
                "id": f"repair-{len(calls)}",
                "object": "chat.completion",
                "created": 1,
                "model": "fixture",
                "choices": [
                    {
                        "index": 0,
                        "message": {
                            "role": "assistant",
                            "content": invalid
                            if len(calls) == 1 or always_invalid
                            else json.dumps(output(proposal(env)), ensure_ascii=False),
                        },
                        "finish_reason": "stop",
                    }
                ],
                "usage": {"prompt_tokens": 10, "completion_tokens": 20},
            },
        )

    env.runtime.models.models = replace(env.models, adapter=protocol_adapter(handler))
    receipt = await env.runtime.assistance.submit(
        env.context,
        AssistanceRequest(message="请创建咨询摘要助手", idempotency_key="repair"),
    )
    return TaskEnvelope(channel_id=env.context.scope.channel_id, run_id=receipt.run_id), calls


@pytest.mark.parametrize(
    ("invalid_kind", "recover"),
    [("extra", False), ("comma", False), ("schema", False), ("extra", True)],
)
async def test_assistance_repairs_original_output_and_error(
    runtime_env, monkeypatch, invalid_kind, recover
):
    env = runtime_env
    valid = json.dumps(output(proposal(env)), ensure_ascii=False)
    invalid = {
        "extra": valid + ' {"extra": true}',
        "comma": valid.replace(",", "", 1),
        "schema": json.dumps({"unexpected": "没有业务结果"}, ensure_ascii=False),
    }[invalid_kind]
    message, calls = await submit_assistance(env, invalid)
    original = env.runs.finish_attempt

    async def crash_after_failure(lease, attempt_id, state, **kwargs):
        saved = await original(lease, attempt_id, state, **kwargs)
        if state == "FAILED":
            # 确认尝试和失败正文已经提交，再模拟进程中断。
            assert saved
            raise RuntimeError("模拟结构修复前中断")
        return saved

    if recover:
        monkeypatch.setattr(env.runs, "finish_attempt", crash_after_failure)
        with pytest.raises(RuntimeError, match="结构修复前中断"):
            await execute_message(env.runs, message, "worker_a", env.runtime)
        async with env.engine.connect() as connection:
            (step,) = await rows(
                connection, "run_steps", env.context.scope.channel_id, run_id=message.run_id
            )
        assert step["state"] == "FAILED" and step["output_ref"] is None
        monkeypatch.setattr(env.runs, "finish_attempt", original)
        await expire_lease(env, message.run_id)

    await execute_message(env.runs, message, "worker_b", env.runtime)
    turn = await env.runtime.assistance.turn(env.context, message.run_id)
    assert turn.state == "SUCCEEDED", turn.error
    assert turn.reply.proposal is not None
    assert not (await env.agents.list_agents(env.context)).items
    assert len(calls) == 2
    assert all("response_format" not in call for call in calls)
    async with env.engine.connect() as connection:
        attempts = sorted(
            await rows(connection, "attempts", env.context.scope.channel_id, run_id=message.run_id),
            key=lambda attempt: attempt["created_at"],
        )
        (failed,) = await rows(
            connection,
            "run_contents",
            env.context.scope.channel_id,
            run_id=message.run_id,
            kind="attempt_failure",
        )
    assert [attempt["state"] for attempt in attempts] == ["FAILED", "SUCCEEDED"]
    assert failed["payload"]["text"] == invalid
    assert calls[1]["messages"][-2] == {"role": "assistant", "content": invalid}
    assert attempts[0]["error"]["message"] in calls[1]["messages"][-1]["content"]
    result = await env.runs.get_run(env.context, message.run_id)
    assert result.usage_summary["complete"] is True
    assert result.usage_summary["attempt_count"] == 2
    assert result.usage_summary["input_tokens"] == 20
    assert result.usage_summary["output_tokens"] == 40


async def test_repeated_invalid_json_never_becomes_success(runtime_env):
    env = runtime_env
    invalid = json.dumps(output(proposal(env)), ensure_ascii=False) + " {}"
    message, calls = await submit_assistance(env, invalid, always_invalid=True)
    await execute_message(env.runs, message, "worker", env.runtime)
    result = await env.runs.get_run(env.context, message.run_id)
    assert result.state == "FAILED" and result.error.code == "MODEL_OUTPUT_INVALID"
    assert "Extra data" in result.error.message
    assert result.result is None and len(calls) == 2
    assert result.usage_summary["complete"] is True
    assert result.usage_summary["attempt_count"] == 2
    assert result.usage_summary["input_tokens"] == 20
    async with env.engine.connect() as connection:
        (step,) = await rows(
            connection, "run_steps", env.context.scope.channel_id, run_id=message.run_id
        )
        failed = await rows(
            connection,
            "run_contents",
            env.context.scope.channel_id,
            run_id=message.run_id,
            kind="attempt_failure",
        )
    assert step["output_ref"] is None
    assert len(failed) == 2


async def test_repair_checks_context_before_another_call(runtime_env):
    env = runtime_env
    receipt, message = await admitted(env)
    env.adapter.responses.append(
        {"unexpected": "中" * (env.definition.context.context_limit * 4 // 3)}
    )
    await execute_message(env.runs, message, "worker", env.runtime)
    result = await env.runs.get_run(env.context, receipt.run_id)
    assert result.state == "FAILED" and result.error.code == "CONTEXT_LIMIT_EXCEEDED"
    assert result.result is None and len(env.adapter.calls) == 1
    assert result.usage_summary["attempt_count"] == 1
    assert result.usage_summary["input_tokens"] == 10
    assert result.usage_summary["complete"] is True
