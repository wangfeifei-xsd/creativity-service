"""INT-F01/F08/F09/F10/F13/F14：并发配置、契约漂移与脱敏验证。"""

import asyncio
import json

import pytest

from creativity_service.core.primitives import ServiceError
from creativity_service.modules.integrations.repositories import repository
from creativity_service.modules.integrations.schemas import ContractTestInput, IntegrationEdit
from tests.integration.channels.conftest import provision

pytestmark = pytest.mark.integration


async def test_configuration_concurrency_scope_and_redacted_tests(integration_env):
    env = integration_env
    service, context = env.bundle.management, env.context
    duplicate = env.connection_body.model_copy(update={"name": "并发连接"})
    outcomes = await asyncio.gather(
        *(service.save(context, duplicate) for _ in range(5)), return_exceptions=True
    )
    assert sum(not isinstance(v, Exception) for v in outcomes) == 1
    assert all(v.code == "INTEGRATION_EXISTS" for v in outcomes if isinstance(v, ServiceError))
    body = ContractTestInput(
        revision=env.connection.revision,
        cases=[{"operation": "dictionary", "arguments": {"keyword": "不应保存的输入"}}],
    )
    test = await service.test(context, env.connection.integration_id, body)
    assert test.state == "PASSED" and test.results[0].item_count == 1
    capabilities = await service.capabilities(context, env.connection.integration_id)
    assert next(c for c in capabilities if c.operation == "dictionary").verified
    assert not next(c for c in capabilities if c.operation == "metric_results").supported
    async with env.engine.connect() as connection:
        stored = await repository(context.scope, "integration_tests").get(connection, test.test_id)
    assert "不应保存" not in json.dumps(stored, default=str, ensure_ascii=False)
    assert "授权游戏名称" not in json.dumps(stored, default=str, ensure_ascii=False)
    edit = IntegrationEdit(
        **env.connection_body.model_dump(), revision=env.connection.revision, status="ACTIVE"
    )
    updated = await service.save(context, edit, env.connection.integration_id)
    assert updated.health_name == "待检测"
    assert not any(c.verified for c in await service.capabilities(context, updated.integration_id))
    with pytest.raises(ServiceError) as failure:
        await service.test(context, env.connection.integration_id, body)
    assert failure.value.code == "REVISION_CONFLICT"
    other = await provision(env, "other", "playmate")
    with pytest.raises(ServiceError) as failure:
        await service.detail(other.manager.context, env.connection.integration_id)
    assert failure.value.status == 404


async def test_schema_change_is_visible_and_config_paths_cannot_escape(
    integration_env, monkeypatch
):
    env = integration_env
    adapter = env.bundle.registry.resolve("fixture", "1").adapter
    old = adapter.dictionary

    async def changed(call):
        result = await old(call)
        return result.model_copy(update={"items": [{"code": "game", "renamed_title": "无效字段"}]})

    monkeypatch.setattr(adapter, "dictionary", changed)
    test = await env.bundle.management.test(
        env.context,
        env.connection.integration_id,
        ContractTestInput(revision=env.connection.revision, cases=[{"operation": "dictionary"}]),
    )
    assert test.state == "FAILED"
    assert test.results[0].code == "BUSINESS_CONTRACT_CHANGED"
    assert (
        await env.bundle.management.detail(env.context, env.connection.integration_id)
    ).health == "DEGRADED"
    for endpoint, path in [
        ("https://evil.example", "/dictionary"),
        ("https://business.example", "/../internal"),
        ("https://business.example", "//evil.example"),
    ]:
        with pytest.raises(ServiceError):
            await env.bundle.management.save(
                env.context,
                env.connection_body.model_copy(
                    update={
                        "business_endpoint": endpoint,
                        "operation_paths": {"dictionary": path},
                    }
                ),
            )


async def test_management_http_and_secrets_are_not_returned(integration_env):
    env = integration_env
    headers = {"Authorization": f"Bearer {env.channel.token.access_token}"}
    response = await env.client.get("/admin/v1/integrations", headers=headers)
    assert response.status_code == 200, response.text
    assert response.json()["items"][0]["data_scope_name"] == "默认业务域"
    response = await env.client.get("/admin/v1/delegation-keys", headers=headers)
    assert response.status_code == 200, response.text
    assert env.key.signing_secret not in response.text
    assert "key_reference" not in response.text
    response = await env.client.get(
        "/admin/v1/integrations",
        headers={"Authorization": f"Bearer {env.identity.token.access_token}"},
    )
    assert response.status_code == 401


async def test_tool_bridge_preserves_run_and_rejects_changed_connection(
    integration_env, monkeypatch
):
    from types import SimpleNamespace

    from creativity_service.integrations.business.registry.tools import BusinessToolAdapter

    env = integration_env
    bridge = BusinessToolAdapter(
        env.bundle.management, env.connection.integration_id, "dictionary", env.connection.revision
    )
    adapter = env.bundle.registry.resolve("fixture", "1").adapter
    original = adapter.dictionary

    async def traced(call):
        assert call.run_id == "actual-run-1"
        assert call.connection["source_scope"] == {"type": "default", "id": "default"}
        return await original(call)

    monkeypatch.setattr(adapter, "dictionary", traced)
    request = SimpleNamespace(context=env.context, arguments={}, run_id="actual-run-1")
    assert (await bridge.invoke(request)).source_request_id == "fixture-source"
    edit = IntegrationEdit(
        **env.connection_body.model_dump(), revision=env.connection.revision, status="ACTIVE"
    )
    await env.bundle.management.save(env.context, edit, env.connection.integration_id)
    with pytest.raises(ServiceError) as failure:
        await bridge.invoke(request)
    assert failure.value.code == "BUSINESS_CONTRACT_CHANGED"


async def test_compatibility_inventory_and_migration_keep_credentials_and_nonce(integration_env):
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
        assert item["legacy_http"][0]["id"] == env.connection.integration_id
        assert item["data_scopes"][0]["external_scope_id"] == "default"
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
