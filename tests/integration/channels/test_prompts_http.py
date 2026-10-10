"""09 真实 IAM、渠道、数据库与 HTTP 边界；对象存储使用测试替身。"""

from types import SimpleNamespace

import pytest

from creativity_service.modules.iam.schemas import ChannelContextInput
from creativity_service.modules.prompts.assembly import build_prompt_service

from .conftest import channel_body, login, provision

pytestmark = pytest.mark.integration


class MemoryStore:
    def __init__(self):
        self.objects = {}

    async def put(self, key, data, content_type):
        self.objects[key] = data

    async def get(self, key, max_bytes):
        return self.objects[key][:max_bytes]

    async def delete(self, key):
        self.objects.pop(key, None)


async def test_prompt_http_permissions_export_and_channel_override(channel_env, channel):
    env = channel_env
    app = env.client._transport.app
    store = MemoryStore()
    service = build_prompt_service(env.engine, env.iam.authorization, store)
    app.state.prompts = service
    app.state.core = SimpleNamespace(artifacts=service.artifacts)
    headers = {"Authorization": f"Bearer {channel.token.access_token}"}
    created = await env.client.post(
        "/admin/v1/prompts",
        headers=headers,
        json={"prompt_code": "risk", "name": "风险提示词", "purpose": "检查内容风险"},
    )
    assert created.status_code == 201, created.text
    prompt_id = created.json()["prompt_id"]
    content = {
        "instruction_blocks": {"system": "遵守规则"},
        "variables": [
            {
                "name": "text",
                "display_name": "业务原文",
                "type": "string",
                "sensitivity": "sensitive",
            }
        ],
        "message_templates": [{"source": "input", "template": "{{ text }}"}],
    }
    configuration = await env.client.get(
        f"/admin/v1/prompts/{prompt_id}/configuration", headers=headers
    )
    draft = await env.client.patch(
        f"/admin/v1/prompts/{prompt_id}/configuration",
        headers=headers,
        json={"revision": configuration.json()["revision"], "content": content},
    )
    assert draft.status_code == 200, draft.text
    version_id = draft.json()["version"]["version_id"]
    invalid = await env.client.post(
        f"/admin/v1/prompt-versions/{version_id}/render", headers=headers, json={"input": {}}
    )
    assert invalid.status_code == 422
    assert invalid.json()["error"]["fields"][0]["message"] == "业务原文：请提供必填值"
    overridden = await env.client.post(
        "/admin/v1/prompts",
        headers=headers,
        json={"prompt_code": "bad", "name": "越权", "purpose": "测试", "channel_id": "other"},
    )
    assert overridden.status_code == 422
    assert (
        await env.client.post(
            f"/admin/v1/prompt-versions/{version_id}/exports",
            headers=headers,
            json={"format": "json"},
        )
    ).status_code == 403
    granted = await env.services.channels.create(
        env.admin,
        channel_body(env, "exports").model_copy(
            update={"independent_actions": ["data:export", "data:read_sensitive"]}
        ),
    )
    _, fresh = await login(env)
    token = await env.iam.sessions.enter(
        fresh,
        ChannelContextInput(
            channel_id=granted.channel_id,
            environment="test",
        ),
    )
    headers = {"Authorization": f"Bearer {token.access_token}"}
    created = await env.client.post(
        "/admin/v1/prompts",
        headers=headers,
        json={"prompt_code": "exported", "name": "可导出提示词", "purpose": "检查脱敏导出"},
    )
    secret_content = {
        **content,
        "variables": [{**content["variables"][0], "default": "不得导出的原文"}],
    }
    exported_id = created.json()["prompt_id"]
    configuration = await env.client.get(
        f"/admin/v1/prompts/{exported_id}/configuration", headers=headers
    )
    draft = await env.client.patch(
        f"/admin/v1/prompts/{exported_id}/configuration",
        headers=headers,
        json={"revision": configuration.json()["revision"], "content": secret_content},
    )
    assert draft.status_code == 200, draft.text
    version_id = draft.json()["version"]["version_id"]
    artifact = await env.client.post(
        f"/admin/v1/prompt-versions/{version_id}/exports", headers=headers, json={"format": "json"}
    )
    assert artifact.status_code == 201, artifact.text
    downloaded = await env.client.get(artifact.json()["download_path"], headers=headers)
    assert downloaded.status_code == 200, downloaded.text
    assert downloaded.headers["cache-control"] == "private, no-store"
    assert set(downloaded.json()) == {"format_version", "content"}
    assert "channel_id" not in downloaded.json()
    assert "不得导出的原文" not in downloaded.text
    other = await provision(env, "second")
    forbidden = await env.client.get(
        f"/admin/v1/prompt-versions/{version_id}",
        headers={"Authorization": f"Bearer {other.token.access_token}"},
    )
    assert forbidden.status_code == 404
    # 管理删除沿用统一资源接口；详情和配置都不能重新读取删除记录。
    summary = await env.client.post(
        "/admin/v1/resource-management/prompt/summaries",
        headers=headers,
        json={"resource_ids": [exported_id]},
    )
    resource = summary.json()[0]
    removed = await env.client.post(
        f"/admin/v1/resource-management/prompt/{exported_id}/delete",
        headers=headers,
        json={
            "revision": resource["revision"],
            "configuration_revision": resource["configuration_revision"],
        },
    )
    assert removed.status_code == 204, removed.text
    assert (
        await env.client.get(f"/admin/v1/prompts/{exported_id}", headers=headers)
    ).status_code == 404
    assert (
        await env.client.get(f"/admin/v1/prompts/{exported_id}/configuration", headers=headers)
    ).status_code == 404
