"""同渠道两域使用同号主体、相同参数和幂等键，仍保持独立结果。"""

import base64
import copy

import pytest

from creativity_service.core.context import TaskEnvelope
from creativity_service.core.database import transaction
from creativity_service.core.locking import record_key
from creativity_service.core.primitives import RunInput, ServiceError
from creativity_service.integrations.business.delegation import sign
from creativity_service.modules.channels.schemas import ClientUpdate, DataScopeCreate, TokenExchange
from creativity_service.modules.iam.repositories import save
from creativity_service.modules.iam.schemas import ChannelContextInput
from creativity_service.modules.integrations.subject_contracts import SubjectReviewSave
from creativity_service.workers.executor import execute_message
from tests.integration.agents.test_agents import publish
from tests.integration.channels.conftest import login
from tests.integration.mcp.test_business_access import adapter_call
from tests.integration.mcp.test_business_access import business_env as business_env
from tests.integration.runtime.test_execution import pytestmark

from .test_capacity import record

__all__ = ["pytestmark"]


async def test_same_channel_two_domains_same_subject_and_idempotency(business_env):
    env = business_env
    channel_id = env.context.scope.channel_id
    second = await env.services.channels.create_data_scope(
        env.admin,
        channel_id,
        DataScopeCreate(
            environment="test",
            name="第二资料域",
            external_scope_type="workspace",
            external_scope_id="domain-two",
            administrator_id=env.user_id,
        ),
    )
    # 隔离测试种子显式授予第二域的敏感读取权；普通管理员不能自行扩大独立授权。
    grant_id = "acceptance_second_domain_sensitive"
    async with transaction(
        env.engine,
        env.context.scope,
        env.iam.access.member_keys(channel_id, env.user_id)
        + [record_key(channel_id, "resource_grants", grant_id)],
    ) as uow:
        await save(
            uow,
            "resource_grants",
            grant_id,
            {
                "grantee_type": "account",
                "grantee_id": env.user_id,
                "resource_type": "data_scope",
                "resource_id": second.data_scope_id,
                "environments": ["test"],
                "data_scopes": [second.data_scope_id],
                "allowed_actions": ["data:read_sensitive"],
            },
        )
    _, platform_session = await login(env)
    entered = await env.iam.sessions.enter(
        platform_session,
        ChannelContextInput(
            channel_id=channel_id, environment="test", data_scope_id=env.tenant.domain.data_scope_id
        ),
    )
    manager = await env.iam.authentication.admin_session(
        entered.access_token, "two-domains", governance=True
    )
    env.context = manager.context
    await env.services.channels.update_client(
        manager,
        channel_id,
        env.identity.client.client_id,
        ClientUpdate(
            revision=env.identity.client.revision,
            data_scopes=[env.tenant.domain.data_scope_id, second.data_scope_id],
        ),
    )
    token = await env.services.keys.exchange(
        TokenExchange(api_key=env.identity.key.api_key), "two-domains"
    )
    identity = await env.iam.authentication.authenticate(token.access_token, "service")
    # 同一接入服务在每个数据域都配置独立的当前主体复核绑定。
    _, platform_session = await login(env)
    second_session = await env.iam.sessions.enter(
        platform_session,
        ChannelContextInput(
            channel_id=channel_id, environment="test", data_scope_id=second.data_scope_id
        ),
    )
    second_manager = await env.iam.authentication.admin_session(
        second_session.access_token, "second-domain", governance=True
    )
    await env.bundle.subject_review.save(
        second_manager.context,
        SubjectReviewSave(
            client_id=env.identity.client.client_id,
            connection_id=env.connections[0].connection_id,
            discovery_id=env.snapshots[0].discovery_id,
            remote_tool_name="access.review-current",
            timeout_seconds=1,
        ),
    )
    detail = await env.agents.create(env.context, env.body)
    await publish(env, detail)
    original = copy.deepcopy(env.sources[0].data)
    entries = []
    for index, domain in enumerate((env.tenant.domain, second)):
        claims = type(env.claims).model_validate(
            {
                **env.claims.model_dump(),
                "data_scope": {"type": domain.external_scope_type, "id": domain.external_scope_id},
                "nonce": f"same-subject-domain-{index}",
            }
        )
        env.sources[0].scope = env.subject.scope.model_copy(
            update={"data_scope_id": domain.data_scope_id}
        ).model_dump()
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
                "data": {"answer": f"资料域{index + 1}"},
                "warnings": [],
                "evidence_refs": [],
            }
        ]
        await execute_message(
            env.runs,
            TaskEnvelope(channel_id=channel_id, run_id=receipt.run_id),
            "domain-worker",
            env.runtime,
        )
        value = await env.runs.get_run(subject, receipt.run_id)
        assert value.state == "SUCCEEDED", value.error
        entries.append((subject, receipt, value))
    first, second = entries
    assert first[0].scope.subject_id == second[0].scope.subject_id
    assert first[1].run_id != second[1].run_id
    assert first[2].result.data != second[2].result.data
    for context, foreign in ((first[0], second[1]), (second[0], first[1])):
        env.sources[0].scope = context.scope.model_dump()
        with pytest.raises(ServiceError) as denied:
            await env.runs.get_run(context, foreign.run_id)
        assert denied.value.status in {403, 404}
    record(
        "two-domains.json",
        {
            "channel_id": channel_id,
            "scope_and_runs": [
                {"scope": context.scope.model_dump(), "run": value.model_dump(mode="json")}
                for context, _, value in entries
            ],
            "same_subject_same_input_same_idempotency": True,
            "cross_domain_query_rejected": True,
            "model": "controlled_fixture",
            "mcp": "real_tcp",
            "authorization_seed": "第二域普通权限经治理入口授予，独立敏感读取权由隔离种子配置",
        },
    )
