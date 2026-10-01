"""交接契约、身份边界及业务摘要验证。"""

import json
from pathlib import Path
from typing import Annotated

import pytest
from fastapi import Depends
from fastapi.testclient import TestClient
from jsonschema import Draft202012Validator
from pydantic import ValidationError

from creativity_service.app import create_app
from creativity_service.core.context import (
    AuthContext,
    ControlScope,
    Scope,
    TaskEnvelope,
    current_context,
    require_http_context,
    task_context,
)
from creativity_service.core.contracts import CONTRACTS, ResultEnvelope, UsageEvent
from creativity_service.core.contracts.export import examples
from creativity_service.core.database import ControlRepository, Repository
from creativity_service.core.database.tables import metadata
from creativity_service.core.deletion import CleanupRegistry
from creativity_service.core.primitives import Money, RunInput, ServiceError, digest
from creativity_service.core.security.keys import CursorCodec, scoped_key


def test_all_schema_examples_and_missing_fields():
    fixtures = examples()
    for contract in CONTRACTS:
        schema = json.loads(Path(f"contracts/core/{contract.__name__}.schema.json").read_text())
        Draft202012Validator.check_schema(schema)
        sample = fixtures[contract.__name__]["success"]
        Draft202012Validator(schema).validate(sample)
        contract.model_validate(sample)
        required = schema.get("required", [])
        if required:
            invalid = {k: v for k, v in sample.items() if k != required[0]}
            with pytest.raises(ValidationError):
                contract.model_validate(invalid)


def test_external_body_cannot_establish_context_or_drop_business_fields():
    with pytest.raises(ValidationError):
        RunInput(agent_code="agent", input={}, channel_id="other")
    a = RunInput(
        agent_code="agent", input={"token": "业务字段", "delivery": "fast"}, delivery="async"
    )
    b = a.model_copy(update={"delivery": "sync"})
    assert a.semantic_digest() == b.semantic_digest()
    assert a.semantic_digest() != RunInput(agent_code="agent", input={}).semantic_digest()
    assert digest({"b": 2, "a": 1}) == digest({"a": 1, "b": 2})
    with pytest.raises(ValueError):
        digest({"number": float("nan")})
    with pytest.raises(ValidationError):
        Money(amount=0.1, currency="CNY")
    assert (
        Money(amount="0.00000001", currency="CNY").model_dump(mode="json")["amount"] == "0.00000001"
    )


def test_scope_control_repository_and_cursor_boundaries():
    scope = Scope(channel_id="first", environment="test")
    other = Scope(channel_id="second", environment="test")
    with pytest.raises(ValidationError):
        Scope(channel_id="system", environment="test")
    with pytest.raises(ServiceError):
        Repository(metadata.tables["artifacts"], None)
    control = ControlScope(purpose="accounts", actor_id="admin")
    with pytest.raises(ServiceError):
        Repository(metadata.tables["artifacts"], control)
    with pytest.raises(ServiceError):
        ControlRepository(metadata.tables["artifacts"], control)
    codec = CursorCodec(b"x" * 32)
    cursor = codec.encode(scope, {"state": "ACTIVE"}, ["time", "id"])
    assert codec.decode(cursor, scope, {"state": "ACTIVE"}) == ["time", "id"]
    for invalid_scope, query in [(other, {"state": "ACTIVE"}), (scope, {"state": "DISABLED"})]:
        with pytest.raises(ServiceError):
            codec.decode(cursor, invalid_scope, query)
    assert scoped_key(scope, "cache", ["tool", "v1"]) != scoped_key(other, "cache", ["tool", "v1"])


async def test_worker_identity_verified_and_cleaned_on_error():
    scope = Scope(channel_id="first", environment="test")
    ctx = AuthContext(
        scope=scope, principal_type="worker", principal_id="worker", request_id="request"
    )

    class Reader:
        async def load_and_authorize(self, message):
            assert current_context.get() is None
            return ctx

    with pytest.raises(RuntimeError):
        async with task_context(TaskEnvelope(channel_id="first", run_id="run"), Reader()):
            assert current_context.get() == ctx
            raise RuntimeError("模拟后台失败")
    assert current_context.get() is None
    with pytest.raises(ServiceError):
        async with task_context(TaskEnvelope(channel_id="second", run_id="run"), Reader()):
            pass
    with pytest.raises(ServiceError):
        async with task_context(TaskEnvelope(channel_id="first", run_id="run"), None):
            pass
    assert current_context.get() is None


def test_authentication_missing_provider_fails_closed(settings):
    app = create_app(settings)

    @app.get("/api/v1/test-protected")
    async def protected(context: Annotated[AuthContext, Depends(require_http_context)]):
        return {"channel_id": context.scope.channel_id}

    with TestClient(app) as client:
        app.state.authentication = None
        response = client.get(
            "/api/v1/test-protected", headers={"Authorization": "Bearer arbitrary"}
        )
    assert response.status_code == 503
    assert response.json()["error"]["code"] == "DEPENDENCY_UNAVAILABLE"


def test_partial_output_is_not_formal_result_and_missing_usage_not_zero():
    sample = examples()["ResultEnvelope"]["success"]
    sample["state"] = "FAILED"
    with pytest.raises(ValidationError):
        ResultEnvelope.model_validate(sample)
    usage = examples()["UsageEvent"]["success"]
    usage["status"] = "MISSING"
    with pytest.raises(ValidationError):
        UsageEvent.model_validate(usage)
    registry = CleanupRegistry()
    with pytest.raises(RuntimeError, match="缺少内容清理处理器"):
        registry.validate({"artifact", "checkpoint"})
