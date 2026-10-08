"""通过真实鉴权接口、直接存储和受控 HTTP 服务验证应用凭据闭环。"""

import asyncio

import pytest

from creativity_service.core.primitives import ServiceError
from creativity_service.core.security.outbound import Destination
from creativity_service.modules.mcp.schemas import McpAuthenticationInput, McpCreate

pytestmark = pytest.mark.integration


@pytest.fixture(autouse=True)
def allow_application_token_endpoint(mcp_env):
    env = mcp_env
    env.mcp.outbound.destinations += (
        Destination(
            env.context.scope.channel_id,
            "test",
            "oauth",
            "127.0.0.1",
            port=env.source.port,
            scheme="http",
            allowed_networks=("127.0.0.1/32",),
            path_prefix="/token",
        ),
    )


async def test_configure_exchange_discover_and_keep_secret_scoped(mcp_env, caplog):
    env = mcp_env
    conn = await env.mcp.create(
        env.context, McpCreate(name="租号服务", endpoint=env.source.endpoint)
    )
    path = f"/admin/v1/mcp-connections/{conn.connection_id}/authentication"
    payload = {
        "revision": conn.revision,
        "token_endpoint": f"http://127.0.0.1:{env.source.port}/token",
        "app_id": env.source.app_id,
        "app_secret": env.source.app_secret,
    }
    saved = await env.client.post(path, json=payload)
    assert saved.status_code == 200, saved.text
    assert env.source.app_secret not in saved.text
    row = await env.mcp.get(env.context, "mcp_connections", conn.connection_id)
    credential = await env.mcp.credential_row(env.context, row["credential_ref"])
    assert credential["secret_value"] == env.source.app_secret
    assert credential["ciphertext"] is None and credential["key_version"] is None
    assert env.mcp.credentials.keys is None
    assert env.source.app_secret not in str(row)
    denied = env.context.model_copy(
        update={"scope": env.context.scope.model_copy(update={"channel_id": "foreign"})}
    )
    with pytest.raises(ServiceError):
        await env.mcp.get(denied, "mcp_connections", conn.connection_id)
    denied = env.context.model_copy(
        update={"scope": env.context.scope.model_copy(update={"environment": "dev"})}
    )
    with pytest.raises(ServiceError):
        await env.mcp.get(denied, "mcp_connections", conn.connection_id)

    checked = await env.mcp.probe(env.context, conn.connection_id, False)
    assert checked.health.value == "HEALTHY"
    discovered = await env.mcp.probe(env.context, conn.connection_id, True)
    assert discovered.tools and env.source.token_exchanges == 1
    assert all(call[1] == f"Bearer {env.source.token}" for call in env.source.calls)
    assert env.source.app_secret not in caplog.text
    assert env.source.token not in caplog.text
    stale = await env.client.post(path, json=payload)
    assert stale.status_code == 409

    # 同一修订并发保存仅一次成功，失败分支清理未绑定凭据。
    current = (await env.mcp.detail(env.context, conn.connection_id)).connection
    payload["revision"] = current.revision
    results = await asyncio.gather(*(env.client.post(path, json=payload) for _ in range(2)))
    assert sorted(response.status_code for response in results) == [200, 409]

    current = (await env.mcp.detail(env.context, conn.connection_id)).connection
    payload["revision"] = current.revision
    payload["app_secret"] = "wrong-application-secret"
    await env.mcp.configure_authentication(
        env.context, conn.connection_id, McpAuthenticationInput(**payload)
    )
    before = len(env.source.calls)
    failure = await env.mcp.probe(env.context, conn.connection_id, False)
    assert failure.error_category == "MCP_AUTH_FAILED"
    assert len(env.source.calls) == before
    assert "wrong-application-secret" not in failure.model_dump_json()


async def test_committed_secret_survives_detail_render_failure(mcp_env, monkeypatch):
    env = mcp_env
    conn = await env.mcp.create(
        env.context, McpCreate(name="凭据提交", endpoint=env.source.endpoint)
    )

    async def failed_view(*args, **kwargs):
        raise ServiceError("UNAVAILABLE", "模拟保存后详情暂不可用", 503)

    monkeypatch.setattr(env.mcp, "view", failed_view)
    with pytest.raises(ServiceError, match="模拟保存后"):
        await env.mcp.configure_authentication(
            env.context,
            conn.connection_id,
            McpAuthenticationInput(
                revision=conn.revision,
                app_id=env.source.app_id,
                app_secret=env.source.app_secret,
                token_endpoint=f"http://127.0.0.1:{env.source.port}/token",
            ),
        )
    row = await env.mcp.get(env.context, "mcp_connections", conn.connection_id)
    assert await env.mcp.credential_row(env.context, row["credential_ref"])
