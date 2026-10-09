"""视觉调试经过统一运行、计量和证据回写，并保留原有文本验证。"""

import pytest

from creativity_service.core.context import TaskEnvelope
from creativity_service.modules.models.repositories import required
from creativity_service.modules.models.schemas import TestInput as CasesInput
from creativity_service.workers.executor import execute_message

pytestmark = [
    pytest.mark.integration,
    pytest.mark.parametrize(
        "agent_env", [{"environment": "test", "context_limit": None}], indirect=True
    ),
]


@pytest.mark.parametrize("correct", [True, False])
async def test_vision_debug_checks_image_answer_and_accounts_usage(runtime_env, correct):
    env = runtime_env
    test = await env.models.testing.create(
        env.tenant.manager, env.model.id, CasesInput(cases=["text", "usage", "vision"])
    )
    assert test.run_id, test.model_dump_json()
    async with env.engine.connect() as connection:
        row = await required(connection, env.context.scope, "model_tests", test.id)
    answer = row["execution"]["cases"][-1]["output_schema"]["const"]
    env.adapter.responses = [{"text": "验证完成"}, {"text": "计量验证"}] + [
        answer if correct else {"shapes": []}
    ] * 3
    await execute_message(
        env.runs,
        TaskEnvelope(channel_id=env.context.scope.channel_id, run_id=test.run_id),
        "worker",
        env.runtime,
    )
    result = await env.models.testing.get(env.tenant.manager, test.id)
    assert result.state == "FIXTURE"
    assert [case.passed for case in result.results] == [True, True, correct]
    request = env.adapter.calls[2][1]
    assert request.requires_vision and not request.requires_native_output
    assert request.messages[0]["content"][1]["image_url"]["url"].startswith(
        "data:image/png;base64,"
    )
    assert all(isinstance(call[1].messages[0]["content"], str) for call in env.adapter.calls[:2])
    run = await env.runs.get_run(env.context, test.run_id)
    assert run.usage_summary["attempt_count"] == len(env.adapter.calls)
    assert run.usage_summary["input_tokens"] == 10 * len(env.adapter.calls)
    model = await env.models.configuration.detail(env.tenant.manager, env.model.id)
    assert next(c for c in model.capabilities if c.capability == "vision").state == "UNVERIFIED"
