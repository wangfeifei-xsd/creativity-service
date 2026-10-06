"""渠道只有环境入口，撤销的数据域与工作区接口不再提供能力。"""

import pytest

from creativity_service.modules.channels.presentation import page_view

from .conftest import credential

pytestmark = pytest.mark.integration


async def test_channel_has_one_environment_entry_and_no_scope_api(channel_env, channel):
    env = channel_env
    cid = channel.channel.channel_id
    page = await page_view(env.services.channels, channel.manager, cid)
    assert "data-scopes" not in {t.navigation_key for t in page.tabs}
    options = await env.iam.sessions.channels(channel.manager)
    assert [(o.channel_id, o.environment) for o in options] == [(cid, "test")]
    headers = {"Authorization": f"Bearer {channel.token.access_token}"}
    for path in ("data-scopes", "environments/test/management-workspace"):
        response = await env.client.post(
            f"/admin/v1/channels/{cid}/{path}", headers=headers, json={}
        )
        assert response.status_code in {404, 405}
    invalid = await env.client.post(
        "/admin/v1/auth/channel-context",
        headers=headers,
        json={"channel_id": cid, "environment": "test", "data_scope_id": "obsolete"},
    )
    assert invalid.status_code == 422


async def test_environment_can_issue_service_credentials_without_mapping(channel_env, channel):
    identity = await credential(channel_env, channel)
    assert identity.context.scope.channel_id == channel.channel.channel_id
    assert identity.context.scope.environment == "test"
    assert "data_scope_id" not in identity.context.scope.model_dump()
