"""已有渠道独立授权必须由平台显式补授，并保持原成员、环境和普通权限。"""

import pytest

from .test_channels import headers

pytestmark = pytest.mark.integration


async def test_platform_explicit_access_and_channel_cannot_self_escalate(channel_env, channel):
    env = channel_env
    base = f"/admin/v1/channels/{channel.channel.channel_id}"
    path = base + "/administrator-access"
    platform = headers(env.admin_token)
    manager = headers(channel.token)
    before = await env.client.get(path, headers=platform)
    assert before.status_code == 200, before.text
    assert before.json()["environments"] == ["test"]
    assert "data:read_sensitive" not in before.json()["independent_actions"]
    body = {
        "revision": before.json()["revision"],
        "independent_actions": before.json()["independent_actions"] + ["data:read_sensitive"],
    }
    assert (await env.client.get(path, headers=manager)).status_code == 403
    assert (await env.client.patch(path, headers=manager, json=body)).status_code == 403
    assert (
        await env.client.patch(path, headers=platform, json={**body, "environment": "prod"})
    ).status_code == 422
    granted = await env.client.patch(path, headers=platform, json=body)
    assert granted.status_code == 200, granted.text
    assert granted.json()["environments"] == ["test"]
    assert set(granted.json()["independent_actions"]) == set(body["independent_actions"])
    stale = await env.client.patch(path, headers=platform, json=body)
    assert stale.status_code == 409, stale.text
    page = await env.client.get(base + "/page", headers=manager)
    actions = {item["action_key"] for item in page.json()["service_actions"]}
    assert {"data:read_sensitive", "run:create", "conversation:read"} <= actions
    created = await env.client.post(
        base + "/clients",
        headers=manager,
        json={
            "name": "匹配服务",
            "environment": "test",
            "scopes": [
                "run:create",
                "run:read",
                "run:content",
                "conversation:read",
                "conversation:write",
                "data:read_sensitive",
            ],
        },
    )
    assert created.status_code == 201, created.text
    removed = await env.client.patch(
        path,
        headers=platform,
        json={
            "revision": granted.json()["revision"],
            "independent_actions": before.json()["independent_actions"],
        },
    )
    assert removed.status_code == 200, removed.text
    page = await env.client.get(base + "/page", headers=manager)
    assert "data:read_sensitive" not in {
        item["action_key"] for item in page.json()["service_actions"]
    }
