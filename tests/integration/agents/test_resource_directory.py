"""具体资源授权与目录使用同一范围，其他渠道和未授权资源不可见。"""

import pytest

from creativity_service.core.primitives import new_id
from creativity_service.modules.channels.resources import ResourceCatalog
from creativity_service.modules.iam.custom_roles import CustomRoles, RoleSave
from creativity_service.modules.iam.schemas import ChannelContextInput, GrantInput, MembershipInput
from tests.integration.channels.test_management_directory import signed_account

pytestmark = pytest.mark.integration


async def test_specific_agent_catalog_grant_and_direct_access(agent_env):
    env = agent_env
    first = await env.agents.create(env.context, env.body)
    second = await env.agents.create(
        env.context,
        env.body.model_copy(
            update={
                "agent_code": "another_agent",
                "name": "另一智能体",
            }
        ),
    )
    identifier = first.agent.agent_id
    other_id = second.agent.agent_id
    scope = env.context.scope
    catalog = ResourceCatalog(env.engine, env.iam.authorization)
    page = await catalog.page(
        env.tenant.manager, scope.channel_id, granting=True, search="业务助手"
    )
    assert [(r.resource_type, r.resource_id) for r in page.items] == [("agent", identifier)]
    assert "agent:manage" in {a.action_key for a in page.items[0].actions}
    role = await CustomRoles(env.iam.access).save(
        env.tenant.manager,
        RoleSave(
            name="指定资源维护",
            allowed_actions=["agent:manage", "version:read", "grant:manage"],
        ),
    )
    account, _, session = await signed_account(env, "resource-reader", [])
    await env.iam.access.put_member(
        env.tenant.manager,
        scope.channel_id,
        account.user_id,
        MembershipInput(
            roles=[role["id"]],
            environments=[scope.environment],
            data_scopes=[scope.data_scope_id],
        ),
    )
    for kind, resource_id, actions in (
        ("channel", scope.channel_id, ["grant:manage"]),
        ("agent", identifier, ["agent:manage", "version:read"]),
    ):
        await env.iam.access.put_grant(
            env.tenant.manager,
            scope.channel_id,
            new_id("grant"),
            GrantInput(
                grantee_type="account",
                grantee_id=account.user_id,
                resource_type=kind,
                resource_id=resource_id,
                allowed_actions=actions,
                environments=[scope.environment],
                data_scopes=[scope.data_scope_id],
            ),
        )
    token = await env.iam.sessions.enter(
        session,
        ChannelContextInput(
            channel_id=scope.channel_id,
            environment=scope.environment,
            data_scope_id=scope.data_scope_id,
        ),
    )
    limited = await env.iam.authentication.admin_session(token.access_token, "resource-directory")
    result = await catalog.page(limited, scope.channel_id, kind="agent", granting=True)
    assert result.total == 1 and result.items[0].resource_id == identifier
    auth = {"Authorization": "Bearer " + token.access_token}
    assert (await env.client.get(f"/admin/v1/agents/{identifier}", headers=auth)).status_code == 200
    assert (await env.client.get(f"/admin/v1/agents/{other_id}", headers=auth)).status_code in {
        403,
        404,
    }
    assert (
        await env.client.get("/admin/v1/channels/other/resource-options", headers=auth)
    ).status_code == 404
    overview = await catalog.references(env.tenant.manager, scope.channel_id)
    assert next(r for r in overview if r.resource_type == "agent").count == 2
