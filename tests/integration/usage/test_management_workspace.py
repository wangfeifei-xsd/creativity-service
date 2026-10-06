"""无业务数据域时可查询管理工作区空用量，不扩展为业务范围通配。"""

from datetime import timedelta

import pytest

from creativity_service.core.primitives import new_id, utcnow
from creativity_service.modules.channels.schemas import ChannelCreate, EnvironmentCreate
from creativity_service.modules.iam.schemas import ChannelContextInput
from tests.integration.channels.conftest import login

pytestmark = pytest.mark.integration


async def test_management_usage_returns_empty_results_in_its_own_scope(usage_env):
    env = usage_env
    channel = await env.services.channels.create(
        env.admin,
        ChannelCreate(name="管理用量渠道", owner="负责人", first_admin_user_id=env.user_id),
    )
    await env.services.channels.create_environment(
        env.admin, channel.channel_id, EnvironmentCreate(environment="dev", name="开发")
    )
    _, session = await login(env)
    token = await env.iam.sessions.enter(
        session,
        ChannelContextInput(
            channel_id=channel.channel_id,
            environment="dev",
        ),
    )
    manager = await env.iam.authentication.admin_session(token.access_token, new_id("request"))
    assert await env.usage.management.scopes(manager) == [manager.context.scope]
    headers = {"Authorization": "Bearer " + token.access_token}
    query = {"start_at": (utcnow() - timedelta(days=1)).isoformat(), "end_at": utcnow().isoformat()}
    summary = await env.client.get("/admin/v1/usage/summary", params=query, headers=headers)
    assert summary.status_code == 200
    assert summary.json()["requests"] == summary.json()["attempts"] == 0
    records = await env.client.get("/admin/v1/usage/records", params=query, headers=headers)
    assert records.status_code == 200 and records.json()["items"] == []
    options = await env.client.get("/admin/v1/usage/options", headers=headers)
    assert options.status_code == 200 and "data_scopes" not in options.json()
    channel_usage = await env.client.get(
        f"/admin/v1/channels/{channel.channel_id}/usage", params=query, headers=headers
    )
    assert channel_usage.status_code == 200
