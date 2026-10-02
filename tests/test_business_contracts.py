"""跨语言签名、源服务错误和严格业务结构转换。"""

import hashlib
import json
import shutil
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from creativity_service.core.context import Scope
from creativity_service.core.primitives import ServiceError, utcnow
from creativity_service.integrations.business.base import BusinessAdapter, BusinessResult
from creativity_service.integrations.business.base.http import StandardHttpAdapter
from creativity_service.integrations.business.base.validation import (
    map_fields,
    resolve_name,
    source_status,
    validate_result,
)
from creativity_service.integrations.business.delegation import (
    DelegationClaims,
    sign,
    split_envelope,
    verify_payload,
)


@pytest.mark.parametrize(
    "status,expected",
    [
        (403, "BUSINESS_FORBIDDEN"),
        (504, "BUSINESS_TIMEOUT"),
        (204, "BUSINESS_NO_DATA"),
        (410, "BUSINESS_UNLISTED"),
        (404, "BUSINESS_NOT_FOUND"),
        (501, "BUSINESS_UNSUPPORTED"),
        (302, "BUSINESS_UNAVAILABLE"),
    ],
)
def test_error_semantics(status, expected):
    with pytest.raises(ServiceError) as failure:
        source_status(status)
    assert failure.value.code == expected


async def test_unimplemented_capability_never_falls_back():
    with pytest.raises(ServiceError) as failure:
        await BusinessAdapter().candidates(None)
    assert failure.value.code == "BUSINESS_UNSUPPORTED"


def test_schema_changes_and_missing_dictionary_names_fail_closed():
    scope = Scope(channel_id="rental", environment="test", data_scope_id="default")
    result = BusinessResult(
        operation="dictionary",
        source_request_id="source-1",
        source_version="1",
        observed_at=utcnow(),
        items=[{"code": "game", "name": "游戏"}],
        has_more=False,
        coverage="全量",
    )
    assert validate_result(result, "dictionary", scope).items[0]["name"] == "游戏"
    for changed in [
        result.model_copy(update={"has_more": True}),
        result.model_copy(update={"items": [{"code": "game"}]}),
        result.model_copy(update={"operation": "candidates"}),
    ]:
        with pytest.raises(ServiceError) as failure:
            validate_result(changed, "dictionary", scope)
        assert failure.value.code == "BUSINESS_CONTRACT_CHANGED"
    with pytest.raises(ServiceError):
        resolve_name("missing", {"game": "游戏"})
    assert map_fields({"displayName": "游戏", "code": "game"}, {"name": "displayName"}) == {
        "name": "游戏",
        "code": "game",
    }
    with pytest.raises(ServiceError):
        map_fields({"changedName": "游戏"}, {"name": "displayName"})


def test_javascript_and_python_signature_vectors():
    vectors = json.loads(Path("contracts/integrations/delegation-vectors.json").read_text())
    node = shutil.which("node") or str(Path("../.tools/js/node_modules/.bin/node").resolve())
    for vector in vectors:
        claims = DelegationClaims.model_validate(vector["claims"])
        expected = sign(claims, vector["kid"], bytes.fromhex(vector["secret_hex"]))
        assert expected == vector["envelope"]
        result = subprocess.run(
            [node, "contracts/integrations/sign-vector.mjs"],
            input=json.dumps(vector),
            text=True,
            capture_output=True,
            check=True,
        )
        assert result.stdout.strip() == expected
        kid, payload, signature = split_envelope(expected)
        assert (
            verify_payload(kid, payload, signature, bytes.fromhex(vector["secret_hex"])) == claims
        )
        assert claims.request.body_sha256 == hashlib.sha256(vector["body"].encode()).hexdigest()


async def test_fixed_outbound_target_and_correlation_headers(monkeypatch):
    scope = Scope(
        channel_id="rental",
        environment="test",
        data_scope_id="default",
        subject_type="MEMBER",
        subject_id="u1",
    )
    from creativity_service.core.context import AuthContext
    from creativity_service.core.security.outbound import ValidatedTarget
    from creativity_service.integrations.business.base import BusinessCall

    context = AuthContext(
        scope=scope,
        principal_type="service",
        principal_id="client1",
        client_id="client1",
        key_id="key1",
        request_id="request1",
    )

    async def validate(received_scope, purpose, url):
        assert received_scope == scope and purpose == "http_tool"
        assert url == "https://business.example/adapter/dictionary"
        return ValidatedTarget(url, "business.example", 443, ("93.184.216.34",), {})

    async def credentials(call):
        return "Bearer fixture-secret"

    adapter = StandardHttpAdapter(SimpleNamespace(validate=validate), credentials)

    async def send(target, headers, body):
        assert headers["X-Request-ID"] == "request1" and headers["X-Run-ID"] == "run1"
        assert json.loads(body)["identity"]["subject_id"] == "u1"
        return 403, {}, b"private upstream response"

    monkeypatch.setattr(adapter, "send", send)
    call = BusinessCall(
        context,
        "run1",
        "dictionary",
        {},
        {
            "business_endpoint": "https://business.example/adapter",
            "operation_paths": {"dictionary": "/dictionary"},
            "contract_version": "1.0.0",
            "source_scope": {"type": "default", "id": "default"},
        },
    )
    with pytest.raises(ServiceError) as failure:
        await adapter.dictionary(call)
    assert failure.value.code == "BUSINESS_FORBIDDEN" and "private" not in failure.value.message
