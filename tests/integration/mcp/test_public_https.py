"""默认装配允许 HTTP 与 HTTPS 离线保存，真实 IAM 与数据库隔离连接和凭据。"""

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
    monkeypatch.setattr(env.mcp.outbound, "resolver", AsyncMock(side_effect=OSError("DNS 不可用")))
    env.client._transport.app.state.mcp = env.mcp
    return env


@pytest.mark.parametrize("scheme", ["http", "https"])
@pytest.mark.parametrize(
    "host", ["gadev.g-buddy.cn", "127.0.0.1:32688", "10.0.0.1", "198.18.0.253", "[::1]"]
)
async def test_connection_and_token_endpoint_save_without_dns_or_allowlist(
    public_mcp_env, scheme, host
):
    env = public_mcp_env
    assert env.mcp.outbound.destinations == ()
    created = await env.client.post(
        "/admin/v1/mcp-connections",
        json={"name": "测试服务", "endpoint": f"{scheme}://{host}/api/mcp"},
    )
    assert created.status_code == 201, created.text
    connection = created.json()
    path = f"/admin/v1/mcp-connections/{connection['connection_id']}"
    updated = await env.client.patch(
        path,
        json={
            "revision": connection["revision"],
            "name": "测试服务",
            "endpoint": f"{scheme}://{host}/api/updated/mcp",
        },
    )
    assert updated.status_code == 200, updated.text
    saved = await env.client.post(
        f"{path}/authentication",
        json={
            "revision": updated.json()["revision"],
            "token_endpoint": f"{scheme}://{host}/mcp/token",
            "app_id": "rental-app",
            "app_secret": "rental-application-secret",
        },
    )
    assert saved.status_code == 200, saved.text
    assert "rental-application-secret" not in saved.text
    assert saved.json()["status"]["value"] == "DISABLED"
    assert env.mcp.outbound.destinations == ()
    env.mcp.outbound.resolver.assert_not_awaited()
    # 实际调用时解析 DNS，代理虚拟 IP 和内网 IP 均可作为连接目标。
    env.mcp.outbound.resolver.side_effect = None
    env.mcp.outbound.resolver.return_value = ["198.18.0.253", "127.0.0.1"]
    row = await env.mcp.get(env.context, "mcp_connections", connection["connection_id"])
    for purpose, url in [
        ("mcp", row["endpoint"]),
        ("oauth", row["authentication"]["token_endpoint"]),
    ]:
        target = await env.mcp.outbound.validate(env.context.scope, purpose, url)
        assert target.addresses == ("198.18.0.253", "127.0.0.1")


@pytest.mark.parametrize("scheme", ["http", "https"])
async def test_public_connection_keeps_management_and_channel_permissions(public_mcp_env, scheme):
    env = public_mcp_env
    body = McpCreate(name="渠道独立连接", endpoint=f"{scheme}://rental.example/api/mcp")
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
                    token_endpoint=f"{scheme}://auth.rental.example/token",
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
