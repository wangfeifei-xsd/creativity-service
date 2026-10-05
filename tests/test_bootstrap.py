"""初始化边界、错误契约和诊断行为验证。"""

import asyncio
import json
import logging
from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient
from pydantic import BaseModel, ValidationError

from creativity_service.api.openapi import build_schema
from creativity_service.app import create_app
from creativity_service.core.config import Settings
from creativity_service.core.infrastructure import Infrastructure
from creativity_service.core.observability import JsonFormatter, request_id_context
from creativity_service.modules.iam.schemas import PasswordChange


def test_missing_config_fails_before_startup(settings, monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    with pytest.raises(ValidationError) as exc:
        create_app()
    assert "database_url" in str(exc.value)
    assert "s3_secret_access_key" in str(exc.value)


def test_redis_namespaces_must_be_distinct(settings):
    values = settings.model_dump()
    values["redis_auth_url"] = values["redis_cache_url"]
    with pytest.raises(ValidationError, match="独立 Redis 数据库"):
        Settings(_env_file=None, **values)


def test_telemetry_requires_exporter_endpoint(settings):
    with pytest.raises(ValidationError, match="OTLP"):
        Settings(_env_file=None, **(settings.model_dump() | {"otel_enabled": True}))


def test_liveness_errors_validation_and_cors(settings):
    app = create_app(settings)

    class Body(BaseModel):
        name: str

    @app.post("/admin/v1/test-input")
    async def validate_body(body: Body):
        return body

    @app.get("/api/v1/test-failure")
    async def failure():
        raise RuntimeError("私密的外部连接凭据")

    with TestClient(app) as client:
        live = client.get("/health/live")
        assert live.status_code == 200
        assert live.json() == {"status": "ok"}
        assert len(live.headers["X-Request-ID"]) == 32
        for path, status in (("/missing", 404), ("/api/v1/test-failure", 500)):
            response = client.get(path, headers={"Origin": "http://localhost:5173"})
            assert response.status_code == status
            assert response.json()["request_id"] == response.headers["X-Request-ID"]
            assert set(response.headers["Access-Control-Expose-Headers"].split(", ")) == {
                "X-Request-ID",
                "Content-Disposition",
            }
            assert "私密" not in response.text
        response = client.post("/admin/v1/test-input", json={})
        assert response.status_code == 422
        assert response.json()["error"]["fields"] == [{"path": ["name"], "message": "请填写此项"}]
        assert "input" not in response.text
    assert request_id_context.get() is None


@pytest.mark.parametrize(
    ("new_password", "message"),
    [("short-pass", "至少输入 12 个字符"), ("p" * 257, "最多输入 256 个字符")],
)
def test_password_length_errors_explain_limits_without_exposing_input(
    settings, new_password, message
):
    app = create_app(settings)

    @app.post("/admin/v1/test-password")
    async def validate_password(body: PasswordChange):
        return {"valid": True}

    current_password = "current-test-password"
    with TestClient(app) as client:
        response = client.post(
            "/admin/v1/test-password",
            json={"current_password": current_password, "new_password": new_password},
        )
    assert response.status_code == 422
    assert response.json()["error"]["fields"] == [{"path": ["new_password"], "message": message}]
    assert current_password not in response.text and new_password not in response.text


@pytest.mark.parametrize("failed", ["database", "redis_auth", "object_storage"])
def test_readiness_identifies_failed_dependency(settings, monkeypatch, failed):
    with TestClient(create_app(settings)) as client:
        infrastructure = client.app.state.infrastructure
        monkeypatch.setattr(infrastructure, "check_database", AsyncMock())
        monkeypatch.setattr(infrastructure, "check_storage", AsyncMock())
        for redis in infrastructure.redis_clients.values():
            monkeypatch.setattr(redis, "ping", AsyncMock())
        if failed == "database":
            infrastructure.check_database.side_effect = ConnectionError("私密连接信息")
        elif failed == "object_storage":
            infrastructure.check_storage.side_effect = ConnectionError("私密连接信息")
        else:
            infrastructure.redis_clients[failed].ping.side_effect = ConnectionError("私密连接信息")
        response = client.get("/health/ready")
        assert response.status_code == 503
        checks = response.json()["checks"]
        assert {key for key, value in checks.items() if value["status"] == "unavailable"} == {
            failed
        }
        assert "私密" not in response.text


async def test_readiness_timeout_is_bounded(settings, monkeypatch):
    infrastructure = Infrastructure(settings)
    infrastructure.timeout = 0.02

    async def slow():
        await asyncio.sleep(1)

    monkeypatch.setattr(infrastructure, "check_database", slow)
    try:
        result = await infrastructure.check_readiness()
        assert result.checks["database"].message == "连接超时"
    finally:
        await infrastructure.close()


def test_schema_export_is_deterministic_and_contains_error_contract():
    first = json.dumps(build_schema(), ensure_ascii=False, sort_keys=True)
    assert first == json.dumps(build_schema(), ensure_ascii=False, sort_keys=True)
    assert "ErrorResponse" in first
    assert "X-Request-ID" in first
    assert "postgresql" not in first


def test_structured_log_has_request_id_and_drops_unapproved_fields():
    record = logging.LogRecord("test", logging.INFO, __file__, 1, "请求完成", (), None)
    record.authorization = "secret"
    token = request_id_context.set("request-test")
    try:
        data = json.loads(JsonFormatter().format(record))
    finally:
        request_id_context.reset(token)
    assert data["request_id"] == "request-test"
    assert "secret" not in json.dumps(data)
