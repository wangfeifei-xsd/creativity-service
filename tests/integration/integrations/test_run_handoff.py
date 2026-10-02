"""18/11 边界：断网重发和 API Key 轮换只产生一次真实运行受理。"""

import asyncio
from datetime import timedelta

import pytest

from creativity_service.core.deletion import RecoveryService
from creativity_service.core.primitives import utcnow
from creativity_service.core.versioning import VersionService
from creativity_service.modules.budgets.services import BudgetService
from creativity_service.modules.channels.schemas import KeyRotate, TokenExchange
from creativity_service.modules.runs.assembly import build_run_service
from creativity_service.modules.runs.repositories import rows
from creativity_service.modules.runs.schemas import (
    ExecutionPolicy,
    ResolvedDefinition,
    RunRequest,
    StepPolicy,
)
from creativity_service.modules.usage.services import UsageService
from tests.integration.core.conftest import TestAuthorization, TestVersionValidator
from tests.integration.integrations.test_delegation import claims, verify

pytestmark = pytest.mark.integration


async def test_delegation_retry_keeps_one_run_across_api_key_rotation(integration_env):
    env = integration_env
    # 发布定义在测试装配中固定；受理与重放仍使用真实 Token、委托及 IAM 授权。
    versions = VersionService(env.engine, TestAuthorization(), TestVersionValidator())
    agent = await versions.create_draft(
        env.context, "agent", "agent-1", "接入边界夹具", {}, [], {"type": "object"}
    )
    agent = await versions.freeze(env.context, agent.version_id, 1)

    class Definition:
        async def resolve(self, context, request):
            return ResolvedDefinition(
                agent_id="agent-1",
                agent_name="接入边界夹具",
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

    budget = BudgetService(env.engine)
    runs = build_run_service(
        env.engine,
        env.iam,
        versions,
        budget,
        UsageService(env.engine, budget),
        resolver=Definition(),
    )
    request = RunRequest(agent_code="test_agent", input={"value": 1})
    from creativity_service.integrations.business.delegation import bind_request

    claim = claims(
        env,
        resources={"agent": ["agent-1"], "run": ["*"]},
        request=bind_request("POST", "/api/v1/runs", request.model_dump_json().encode(), "retry-1"),
    )
    context = await verify(env, claim)
    await RecoveryService(env.engine, TestAuthorization()).initialize_fresh(context)
    receipts = await asyncio.gather(
        *(runs.admit_run(context, request, "retry-1") for _ in range(4))
    )
    assert len({receipt.run_id for receipt in receipts}) == 1
    issued = await env.services.keys.rotate(
        env.channel.manager,
        env.channel.channel.channel_id,
        env.identity.key.key.key_id,
        KeyRotate(
            revision=env.identity.key.key.revision,
            overlap_seconds=60,
            expires_at=utcnow() + timedelta(days=1),
        ),
    )
    token = await env.services.keys.exchange(TokenExchange(api_key=issued.api_key), "rotate-retry")
    service_context = await env.iam.authentication.authenticate(token.access_token, "service")
    repeated = await runs.admit_run(
        await verify(env, claim, context=service_context), request, "retry-1"
    )
    assert repeated.run_id == receipts[0].run_id
    async with env.engine.connect() as connection:
        stored = await rows(connection, "runs", context.scope.channel_id)
        outbox = await rows(connection, "dispatch_outbox", context.scope.channel_id)
    assert len(stored) == len(outbox) == 1
    assert stored[0]["identity"]["delegation_id"] == context.delegation_id
    assert stored[0]["identity"]["token_digest"] is None
