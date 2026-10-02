"""真实 IAM 和 Redis Token 到期不取消已受理的管理运行。"""

import pytest

from creativity_service.core.context import TaskEnvelope
from creativity_service.core.primitives import RunInput, ServiceError
from creativity_service.core.versioning import VersionService
from creativity_service.modules.budgets.services import BudgetService
from creativity_service.modules.runs.assembly import build_run_service
from creativity_service.modules.runs.schemas import ExecutionPolicy, ResolvedDefinition, StepPolicy
from creativity_service.modules.usage.services import UsageService
from tests.integration.channels.conftest import channel_env, provision
from tests.integration.core.conftest import TestAuthorization, TestVersionValidator
from tests.integration.runs.conftest import InternalDefinition
from tests.integration.runs.test_runs import result

pytestmark = pytest.mark.integration
__all__ = ["channel_env"]


async def test_accepted_worker_uses_current_identity_without_expired_login_token(channel_env):
    env = channel_env
    channel = await provision(env)
    context = channel.manager.context
    versions = VersionService(env.engine, TestAuthorization(), TestVersionValidator())
    schema = {"type": "object"}
    agent = await versions.create_draft(context, "agent", "agent_one", "测试定义", {}, [], schema)
    agent = await versions.freeze(context, agent.version_id, 1)
    resolver = InternalDefinition(
        ResolvedDefinition(
            agent_id="agent_one",
            agent_name="令牌到期验收",
            version_ids=(agent.version_id,),
            input_schema=schema,
            output_schema=schema,
            policy=ExecutionPolicy(
                steps=(
                    StepPolicy(
                        node_key="compute", kind="compute", target_version_id=agent.version_id
                    ),
                )
            ),
        )
    )
    budgets = BudgetService(env.engine)
    runs = build_run_service(
        env.engine, env.iam, versions, budgets, UsageService(env.engine, budgets), resolver=resolver
    )
    receipt = await runs.admit_run(context, RunInput(agent_code="test", input={}), "login_expiry")
    token_key = env.iam.authentication.tokens.token_key(context.token_digest)
    assert await env.redis.expire(token_key, 0)
    with pytest.raises(ServiceError):
        await runs.get_run(context, receipt.run_id)
    message = TaskEnvelope(channel_id=context.scope.channel_id, run_id=receipt.run_id)
    lease = await runs.claim_lease(message, "worker")
    assert lease is not None
    stored = await runs.load(message)
    assert stored["identity"]["session_id"] is None and stored["identity"]["token_digest"] is None
    assert await runs.finish_run(lease, "SUCCEEDED", result()) == "SUCCEEDED"
