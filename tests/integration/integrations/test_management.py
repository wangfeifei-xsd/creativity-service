"""委托管理保留密钥边界，渠道盘点不依赖旧业务连接。"""

import json

import pytest

pytestmark = pytest.mark.integration


@pytest.mark.parametrize(
    "method,path",
    [
        ("GET", "/admin/v1/integrations"),
        ("POST", "/admin/v1/integrations"),
        ("GET", "/admin/v1/integrations/options"),
        ("GET", "/admin/v1/integrations/legacy"),
        ("PATCH", "/admin/v1/integrations/legacy"),
        ("GET", "/admin/v1/integrations/legacy/capabilities"),
        ("GET", "/admin/v1/integrations/legacy/tests"),
        ("POST", "/admin/v1/integrations/legacy/tests"),
        ("POST", "/admin/v1/integration-credentials"),
    ],
)
async def test_removed_management_routes_are_unavailable(integration_env, method, path):
    env = integration_env
    response = await env.client.request(
        method,
        path,
        headers={"Authorization": f"Bearer {env.channel.token.access_token}"},
    )
    assert response.status_code == 404
    assert path not in env.client._transport.app.openapi()["paths"]


async def test_delegation_management_redacts_secrets(integration_env):
    env = integration_env
    response = await env.client.get(
        "/admin/v1/delegation-keys",
        headers={"Authorization": f"Bearer {env.channel.token.access_token}"},
    )
    assert response.status_code == 200, response.text
    assert env.key.signing_secret not in response.text
    assert "key_reference" not in response.text


async def test_access_inventory_and_migration_keep_credentials_and_nonce(integration_env):
    from alembic.config import Config

    from alembic import command
    from scripts.inventory_access import inventory
    from tests.integration.integrations.test_delegation import claims, verify

    env = integration_env
    claim = claims(env)
    original = await verify(env, claim)

    def roundtrip(connection):
        before = inventory(connection)
        item = before["channels"][0]
        assert item["keys"][0]["id"] == env.identity.key.key.key_id
        assert "legacy_http" not in item
        assert "data_scopes" not in item
        assert before["record_fingerprints"]["delegation_nonces"]["count"] == 1
        assert env.identity.key.api_key not in json.dumps(before)
        assert env.key.signing_secret not in json.dumps(before)
        config = Config("alembic.ini")
        config.set_main_option("version_table_schema", env.schema)
        config.attributes["connection"] = connection
        command.upgrade(config, "head")
        command.upgrade(config, "head")
        assert inventory(connection) == before

    async with env.engine.begin() as connection:
        await connection.run_sync(roundtrip)
    current = await verify(env, claim)
    assert current.scope == original.scope
    assert current.delegation_id == original.delegation_id
    assert current.client_id == original.client_id
