"""06 页面选项与切换使用真实 PostgreSQL、Redis 和原授权服务。"""

import pytest

from creativity_service.modules.iam.schemas import AccountCreate, ChannelContextInput, LoginInput
from tests.support.captcha import captcha_token

from .conftest import INITIAL, channel_body, login, provision

pytestmark = pytest.mark.integration


async def test_chn_page_options_and_platform_return(channel_env, channel):
    env = channel_env
    auth = {"Authorization": f"Bearer {channel.token.access_token}"}
    path = f"/admin/v1/channels/{channel.channel.channel_id}"
    page = await env.client.get(path + "/page", headers=auth)
    assert page.status_code == 200
    assert "key:create" in {a["action_key"] for a in page.json()["actions"]}
    assert "members" in {t["navigation_key"] for t in page.json()["tabs"]}
    other = await provision(env, "second")
    hidden = await env.client.get(
        f"/admin/v1/channels/{other.channel.channel_id}/page", headers=auth
    )
    assert hidden.status_code == 404
    options = await env.client.get(path + "/access-options", headers=auth)
    assert options.status_code == 200
    assert all(o["channel_id"] == channel.channel.channel_id for o in options.json()["workspaces"])
    assert set(options.json()["accounts"][0]) == {"value", "label"}
    response = await env.client.post("/admin/v1/auth/platform-context", headers=auth)
    assert response.status_code == 200
    assert (await env.client.get("/admin/v1/auth/session", headers=auth)).status_code == 401
    platform = {"Authorization": f"Bearer {response.json()['access_token']}"}
    view = await env.client.get("/admin/v1/auth/session", headers=platform)
    assert view.json()["workspace"] is None
    governance = await env.client.get(path + "/page", headers=platform)
    assert "key:create" not in {a["action_key"] for a in governance.json()["actions"]}
    assert "key:revoke" in {a["action_key"] for a in governance.json()["actions"]}
    assert "data_scope_types" not in governance.json()


async def test_initial_password_and_creation_options_require_server_authority(channel_env):
    env = channel_env
    account = await env.iam.accounts.create(
        env.admin,
        AccountCreate(
            login_name="initial-user",
            display_name="待改密账号",
            initial_password=INITIAL,
        ),
    )
    token = await env.iam.sessions.login(
        LoginInput(
            login_name="initial-user",
            password=INITIAL,
            captcha_token=await captcha_token(env.iam, "initial-user", "ui"),
        ),
        "ui",
        "ui-request",
    )
    auth = {"Authorization": f"Bearer {token.access_token}"}
    initial = await env.client.get("/admin/v1/auth/session", headers=auth)
    assert initial.status_code == 403
    assert initial.json()["error"]["code"] == "PASSWORD_CHANGE_REQUIRED"
    assert (
        await env.client.get("/admin/v1/channel-create-options", headers=auth)
    ).status_code == 403
    token, _ = await login(env)
    authorized = await env.client.get(
        "/admin/v1/channel-create-options",
        headers={"Authorization": f"Bearer {token.access_token}"},
    )
    assert authorized.status_code == 200
    assert any(o["value"] == account.user_id for o in authorized.json()["accounts"])
    assert all(set(o) == {"value", "label"} for o in authorized.json()["accounts"])


async def test_iam_a09_a13_a14_direct_calls_and_same_member_records(channel_env, channel):
    env = channel_env
    token, _ = await login(env)
    switched = await env.client.post(
        "/admin/v1/auth/channel-context",
        headers={"Authorization": f"Bearer {token.access_token}"},
        json=ChannelContextInput(
            channel_id=channel.channel.channel_id,
            environment="test",
            data_scope_id=channel.domain.data_scope_id,
        ).model_dump(),
    )
    auth = {"Authorization": f"Bearer {switched.json()['access_token']}"}
    path = f"/admin/v1/channels/{channel.channel.channel_id}"
    overview = await env.client.get(path + "/overview", headers=auth)
    direct = await env.client.get(path + "/members", headers=auth)
    embedded = await env.client.get(overview.json()["members_path"], headers=auth)
    assert direct.json() == embedded.json()
    denied = await env.client.post(
        "/admin/v1/channels",
        headers=auth,
        json={
            **channel_body(env, "forbidden").model_dump(),
            "roles": ["platform_admin"],
        },
    )
    assert denied.status_code == 422
    changed = await env.client.get(
        path + "/page", headers={**auth, "X-Channel-ID": "another-channel"}
    )
    assert changed.json()["channel"]["channel_id"] == channel.channel.channel_id
