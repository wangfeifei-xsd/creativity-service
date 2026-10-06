"""同渠道环境内不同主体使用相同参数和幂等键，仍保持独立结果。"""

import base64
import copy

import pytest

from creativity_service.core.context import TaskEnvelope
from creativity_service.core.primitives import RunInput, ServiceError
from creativity_service.integrations.business.delegation import sign
from creativity_service.workers.executor import execute_message
from tests.integration.agents.test_agents import publish
from tests.integration.mcp.test_business_access import adapter_call
from tests.integration.mcp.test_business_access import business_env as business_env
from tests.integration.runtime.test_execution import pytestmark

from .test_capacity import record

__all__ = ["pytestmark"]


async def test_same_channel_environment_distinct_subjects_and_idempotency(business_env):
    env = business_env
    channel_id = env.context.scope.channel_id
    identity = env.identity.context
    detail = await env.agents.create(env.context, env.body)
    await publish(env, detail)
    original = copy.deepcopy(env.sources[0].data)
    entries = []
    for index, subject_id in enumerate((env.subject.scope.subject_id, "reader-b")):
        claims = type(env.claims).model_validate(
            {
                **env.claims.model_dump(),
                "nonce": f"same-environment-subject-{index}",
                "subject_id": subject_id,
            }
        )
        env.sources[0].scope = env.subject.scope.model_copy(
            update={"subject_id": subject_id}
        ).model_dump()
        env.sources[0].permissions[subject_id] = {
            "actions": claims.actions,
            "resources": claims.resources,
        }
        subject = await env.bundle.delegation.verify(
            identity,
            sign(claims, env.key.key.kid, base64.b64decode(env.key.signing_secret)),
            claims.request,
        )
        raw = await adapter_call(env, context=subject)
        assert raw.data == original
        receipt = await env.runs.admit_run(
            subject,
            RunInput(agent_code=env.body.agent_code, input={"request": "同号资料"}),
            "same-key",
        )
        env.adapter.responses = [
            {
                "business_status": "COMPLETED",
                "schema_version": "1.0",
                "data": {"answer": f"主体结果{index + 1}"},
                "warnings": [],
                "evidence_refs": [],
            }
        ]
        await execute_message(
            env.runs,
            TaskEnvelope(channel_id=channel_id, run_id=receipt.run_id),
            "subject-worker",
            env.runtime,
        )
        value = await env.runs.get_run(subject, receipt.run_id)
        assert value.state == "SUCCEEDED", value.error
        entries.append((subject, receipt, value))
    first, second = entries
    assert first[0].scope.subject_id != second[0].scope.subject_id
    assert first[1].run_id != second[1].run_id
    assert first[2].result.data != second[2].result.data
    for context, foreign in ((first[0], second[1]), (second[0], first[1])):
        env.sources[0].scope = context.scope.model_dump()
        with pytest.raises(ServiceError) as denied:
            await env.runs.get_run(context, foreign.run_id)
        assert denied.value.status in {403, 404}
    record(
        "two-subjects.json",
        {
            "channel_id": channel_id,
            "scope_and_runs": [
                {"scope": context.scope.model_dump(), "run": value.model_dump(mode="json")}
                for context, _, value in entries
            ],
            "different_subject_same_input_same_idempotency": True,
            "cross_subject_query_rejected": True,
            "model": "controlled_fixture",
            "mcp": "real_tcp",
            "authorization_seed": "主体权限由受信源服务复核，同渠道环境内仍按主体隔离",
        },
    )
