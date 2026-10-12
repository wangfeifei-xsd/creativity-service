"""默认装配允许公网 HTTPS 保存，真实 IAM 与数据库继续隔离连接和凭据。"""

from unittest.mock import AsyncMock

import pytest

from creativity_service.core.primitives import ServiceError
from creativity_service.modules.mcp.assembly import McpSettings, build_mcp_service
from creativity_service.modules.mcp.schemas import McpAuthenticationInput, McpCreate, McpEdit
from tests.integration.channels.conftest import provision

pytestmark = pytest.mark.integration


@pytest.fixture
def public_mcp_env(tools_env, monkeypatch):
    env = tools_env
    env.mcp = build_mcp_service(
        env.engine, env.iam.authorization, env.tools, settings=McpSettings(_env_file=None)
    )
    monkeypatch.setattr(env.mcp.outbound, "resolver", AsyncMock(return_value=["93.184.216.34"]))
    env.client._transport.app.state.mcp = env.mcp
    return env


async def test_public_connection_and_token_endpoint_save_without_allowlist(public_mcp_env):
    env = public_mcp_env
    assert env.mcp.outbound.destinations == ()
    created = await env.client.post(
        "/admin/v1/mcp-connections",
        json={"name": "公网租号服务", "endpoint": "https://rental.example/api/mcp"},
    )
    assert created.status_code == 201, created.text
    connection = created.json()
    path = f"/admin/v1/mcp-connections/{connection['connection_id']}"
    updated = await env.client.patch(
        path,
        json={
            "revision": connection["revision"],
            "name": "公网租号服务",
            "endpoint": "https://rental.example:8443/api/mcp",
        },
    )
    assert updated.status_code == 200, updated.text
    saved = await env.client.post(
        f"{path}/authentication",
        json={
            "revision": updated.json()["revision"],
            "token_endpoint": "https://auth.rental.example/api/mcp/token",
            "app_id": "rental-app",
            "app_secret": "rental-application-secret",
        },
    )
    assert saved.status_code == 200, saved.text
    assert "rental-application-secret" not in saved.text
    assert saved.json()["status"]["value"] == "DISABLED"
    assert env.mcp.outbound.destinations == ()
    # 请求前仍重新校验，不能沿用保存时的公网 DNS 结果访问内网。
    env.mcp.outbound.resolver.return_value = ["127.0.0.1"]
    row = await env.mcp.get(env.context, "mcp_connections", connection["connection_id"])
    with pytest.raises(ServiceError) as error:
        await env.mcp.client_credentials.http.post(
            env.context.scope, "oauth", row["authentication"]["token_endpoint"], b""
        )
    assert error.value.code == "DESTINATION_FORBIDDEN"


async def test_public_connection_keeps_management_and_channel_permissions(public_mcp_env):
    env = public_mcp_env
    body = McpCreate(name="渠道独立连接", endpoint="https://rental.example/api/mcp")
    conn = await env.mcp.create(env.context, body)
    foreign = await provision(env, "second")
    other = await env.iam.authentication.authenticate(foreign.token.access_token, "management")
    for context in [other, env.context.model_copy(update={"principal_type": "service"})]:
        with pytest.raises(ServiceError) as error:
            await env.mcp.edit(
                context,
                conn.connection_id,
                McpEdit(**body.model_dump(), revision=conn.revision),
            )
        assert error.value.status in {403, 404}
        with pytest.raises(ServiceError) as error:
            await env.mcp.configure_authentication(
                context,
                conn.connection_id,
                McpAuthenticationInput(
                    revision=conn.revision,
                    app_id="other-app",
                    app_secret="other-application-secret",
                    token_endpoint="https://auth.rental.example/token",
                ),
            )
        assert error.value.status in {403, 404}
    with pytest.raises(ServiceError) as error:
        await env.mcp.create(env.context.model_copy(update={"principal_type": "service"}), body)
    assert error.value.code == "FORBIDDEN"
    for scope in [other.scope, env.context.scope.model_copy(update={"environment": "prod"})]:
        with pytest.raises(ServiceError) as error:
            await env.mcp.get(
                env.context.model_copy(update={"scope": scope}),
                "mcp_connections",
                conn.connection_id,
            )
        assert error.value.code == "NOT_FOUND"
