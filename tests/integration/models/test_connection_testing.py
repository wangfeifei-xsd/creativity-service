"""管理工作区连接检查不依赖业务运行授权，且保留并发与渠道边界。"""

import pytest
from sqlalchemy import func, select

from creativity_service.core.database import assert_external_io_allowed
from creativity_service.core.primitives import ServiceError
from creativity_service.modules.iam.schemas import ChannelContextInput
from creativity_service.modules.models import connection_testing
from creativity_service.modules.models.repositories import repository
from creativity_service.storage import metadata
from tests.integration.channels.conftest import login, provision
from tests.integration.models.test_models import setup

pytestmark = pytest.mark.integration


async def test_management_workspace_can_check_connection_without_business_execution(
    channel_env, monkeypatch
):
    env = channel_env
    tenant, services, _, connection, _, model = await setup(env)
    _, session = await login(env)
    token = await env.iam.sessions.enter(
        session,
        ChannelContextInput(
            channel_id=tenant.channel.channel_id,
            environment="test",
        ),
    )
    manager = await env.iam.authentication.admin_session(token.access_token, "connection-test")
    listed = await services.configuration.models(manager)
    actions = {item.action_key: item for item in listed.items[0].actions}
    assert actions["test_connection"].enabled
    assert actions["test"].enabled
    calls = []

    async def probe(config, target, secret):
        assert_external_io_allowed()
        assert config.model_id == model.id and target.hostname == "models.example"
        assert secret.get_secret_value() == b"never-return-this-secret"
        calls.append(config)

    monkeypatch.setattr(connection_testing, "probe_connection", probe)
    response = await env.client.post(
        f"/admin/v1/models/{model.id}/connection-test",
        headers={"Authorization": "Bearer " + token.access_token},
    )
    assert response.status_code == 200, response.text
    assert response.json()["success"] is True and response.json()["latency_ms"] >= 0
    assert len(calls) == 1 and "never-return-this-secret" not in response.text
    async with env.engine.connect() as db:
        saved = await repository(manager.context.scope, "model_connections").get(db, connection.id)
        mapped = await repository(manager.context.scope, "models").get(db, model.id)
        assert saved["health_status"] == "HEALTHY" and saved["health_checked_at"] is not None
        assert mapped["capabilities"] == {} and mapped["verified_at"] is None
        assert await db.scalar(select(func.count()).select_from(metadata.tables["runs"])) == 0


async def test_failed_check_is_recorded_with_safe_reason(channel_env, monkeypatch):
    tenant, services, _, connection, _, model = await setup(channel_env)

    async def probe(*args):
        raise ServiceError("MODEL_AUTH_FAILED", "raw-secret-never-expose", 401)

    monkeypatch.setattr(connection_testing, "probe_connection", probe)
    result = await services.connection_testing.check(tenant.manager, model.id)
    assert not result.success and result.error_code == "MODEL_AUTH_FAILED"
    assert "raw-secret" not in result.message
    saved = (await services.configuration.connections(tenant.manager)).items[0]
    assert saved.id == connection.id and saved.health_status == "UNAVAILABLE"
    assert "凭据" in saved.health_reason


async def test_stale_check_cannot_override_updated_connection(channel_env, monkeypatch):
    tenant, services, body, connection, _, model = await setup(channel_env)

    async def probe(*args):
        await services.configuration.save_connection(
            tenant.manager,
            body.model_copy(update={"revision": connection.revision, "name": "已更新连接"}),
            connection.id,
        )

    monkeypatch.setattr(connection_testing, "probe_connection", probe)
    with pytest.raises(ServiceError, match="配置已变化"):
        await services.connection_testing.check(tenant.manager, model.id)
    saved = (await services.configuration.connections(tenant.manager)).items[0]
    assert saved.name == "已更新连接" and saved.health_status == "UNKNOWN"


async def test_cross_channel_and_disabled_models_do_not_send_probe(channel_env, monkeypatch):
    tenant, services, _, _, model_body, model = await setup(channel_env)

    async def probe(*args):
        pytest.fail("未经授权或停用的模型不应发送请求")

    monkeypatch.setattr(connection_testing, "probe_connection", probe)
    other = await provision(channel_env, "other", "club")
    with pytest.raises(ServiceError) as missing:
        await services.connection_testing.check(other.manager, model.id)
    assert missing.value.status == 404
    await services.configuration.save_model(
        tenant.manager,
        model_body.model_copy(update={"revision": model.revision, "status": "DISABLED"}),
        model.id,
    )
    disabled = (await services.configuration.models(tenant.manager)).items[0]
    action = next(a for a in disabled.actions if a.action_key == "test_connection")
    assert not action.enabled and "停用" in action.disabled_reason
    with pytest.raises(ServiceError, match="停用"):
        await services.connection_testing.check(tenant.manager, model.id)
