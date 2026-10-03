"""内部暂停释放租约，重启后的原运行显式恢复与并发确认边界。"""

import asyncio
from datetime import timedelta

import pytest

from creativity_service.core.database import transaction
from creativity_service.core.primitives import ServiceError, utcnow
from creativity_service.modules.agents.schemas import AgentEdge, AgentStep
from creativity_service.modules.runs.interruptions import ResumeInput
from creativity_service.modules.runs.repositories import save
from creativity_service.workers.executor import execute_message
from tests.integration.runtime.test_execution import admitted

pytestmark = [
    pytest.mark.integration,
    pytest.mark.parametrize(
        "agent_env",
        [
            {
                "environment": "test",
                "independent_actions": ["release:publish", "data:read_sensitive", "run:approve"],
            }
        ],
        indirect=True,
    ),
]


async def paused(env, operator="input", workflow="stateful"):
    schema = {
        "type": "object",
        "properties": {"answer": {"type": "string"}},
        "required": ["answer"],
        "additionalProperties": False,
    }
    step = AgentStep(
        key="confirmation",
        name="确认业务参数",
        kind="compute",
        operator=operator,
        input_schema=schema if operator == "approval" else {"type": "object", "properties": {}},
        output_schema=schema,
        inputs={"answer": {"source": "constant", "value": "待确认参数"}}
        if operator == "approval"
        else {},
        failure_policy="fail",
        max_retries=0,
    )
    definition = env.definition.model_copy(
        update={
            "workflow_type": workflow,
            "entrypoint": "stateful.v1" if workflow == "stateful" else "workflow.v1",
            "start_step": step.key,
            "steps": (step, *env.definition.steps),
            "edges": (AgentEdge(source=step.key, target="answer"), *env.definition.edges),
        }
    )
    receipt, message = await admitted(env, definition=definition)
    await execute_message(env.runs, message, "pause-worker", env.runtime)
    detail = await env.runs.get_run(env.context, receipt.run_id)
    assert detail.state == ("WAITING_APPROVAL" if operator == "approval" else "WAITING_INPUT"), (
        detail.model_dump()
    )
    assert await env.runs.claim_lease(message, "duplicate-worker") is None
    view = await env.runs.interruption(env.context, receipt.run_id)
    body = ResumeInput(
        interruption_id=view.interruption_id,
        revision=view.revision,
        confirmation_digest=view.confirmation_digest,
        decision="approve" if operator == "approval" else "respond",
        input={} if operator == "approval" else {"answer": "补充内容"},
        idempotency_key="resume-once",
    )
    return receipt, message, body


@pytest.mark.parametrize("operator,workflow", [("input", "stateful"), ("approval", "template")])
async def test_resume_original_run_concurrent_and_stale_confirmation(
    runtime_env, operator, workflow
):
    env = runtime_env
    receipt, message, body = await paused(env, operator, workflow)
    with pytest.raises(ServiceError) as failure:
        await env.runs.resume(
            env.context, receipt.run_id, body.model_copy(update={"confirmation_digest": "0" * 64})
        )
    assert failure.value.code == "CONFIRMATION_STALE"
    results = await asyncio.gather(
        *(env.runs.resume(env.context, receipt.run_id, body) for _ in range(3))
    )
    assert all(r.run_id == receipt.run_id for r in results)
    with pytest.raises(ServiceError) as conflict:
        await env.runs.resume(
            env.context, receipt.run_id, body.model_copy(update={"idempotency_key": "different"})
        )
    assert conflict.value.code == "IDEMPOTENCY_CONFLICT"
    await execute_message(env.runs, message, "restarted-worker", env.runtime)
    result = await env.runs.get_run(env.context, receipt.run_id)
    assert result.state == "SUCCEEDED", result.model_dump()
    assert len(env.adapter.calls) == 1


@pytest.mark.parametrize("stop", ["cancel", "expire", "reject"])
async def test_waiting_cancellation_expiration_and_rejection(runtime_env, stop):
    env = runtime_env
    receipt, message, body = await paused(env, "approval")
    if stop == "cancel":
        await env.runs.cancel(env.context, receipt.run_id)
    elif stop == "reject":
        await env.runs.resume(
            env.context, receipt.run_id, body.model_copy(update={"decision": "reject"})
        )
    else:
        async with transaction(
            env.engine, env.context.scope, env.runs.keys(env.context, receipt.run_id)
        ) as uow:
            await save(uow, "runs", receipt.run_id, {"deadline": utcnow() - timedelta(seconds=1)})
        await env.runs.reconcile_run(message)
    with pytest.raises(ServiceError):
        await env.runs.resume(env.context, receipt.run_id, body)
    await execute_message(env.runs, message, "late-worker", env.runtime)
    assert not env.adapter.calls
