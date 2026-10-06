"""独立客户端验证签名兼容、失联重发和逐帧恢复，不能替代真实端到端证据。"""

import hashlib
import json
from pathlib import Path

import httpx
import pytest

from creativity_service.api.openapi import build_schema
from creativity_service.integrations.business.delegation import split_envelope, verify_payload
from examples.backend.client import BusinessBackendClient, PlatformError, Principal, sign_claims


class Principals:
    async def current(self):
        return Principal(
            "user",
            "a",
            ["run:create", "run:read", "run:content"],
            {"run": ["*"], "agent": ["*"]},
        )


def client(handler):
    return BusinessBackendClient(
        "https://platform.example",
        "server-api-key",
        "dk_test",
        b"a" * 32,
        "source",
        "creativity-api",
        Principals(),
        transport=httpx.MockTransport(handler),
    )


def claims(request):
    kid, payload, signature = split_envelope(request.headers["X-Business-Delegation"])
    value = verify_payload(kid, payload, signature, b"a" * 32)
    assert value.request.method == request.method
    assert value.request.target == request.url.raw_path.decode()
    assert value.request.body_sha256 == hashlib.sha256(request.content).hexdigest()
    assert value.request.idempotency_key == request.headers.get("Idempotency-Key")
    return value


def error(status, code):
    return httpx.Response(
        status, json={"error": {"code": code, "message": "测试错误"}, "request_id": "request-test"}
    )


def test_standalone_signature_matches_published_vectors():
    for item in json.loads(Path("contracts/integrations/delegation-vectors.json").read_text()):
        assert (
            sign_claims(item["claims"], item["kid"], bytes.fromhex(item["secret_hex"]))
            == item["envelope"]
        )


async def test_lost_response_then_expired_token_resigns_same_body_and_key():
    exchanges, sent = [], []

    def handler(request):
        if request.url.path == "/api/v1/auth/token":
            exchanges.append(request)
            assert "X-Business-Delegation" not in request.headers
            return httpx.Response(
                200, json={"access_token": f"opaque-{len(exchanges)}", "expires_in": 60}
            )
        sent.append((request, claims(request)))
        if len(sent) == 1:
            raise httpx.ReadError("响应丢失")
        if len(sent) == 2:
            return error(401, "UNAUTHENTICATED")
        return httpx.Response(202, json={"run_id": "run_original", "state": "QUEUED"})

    async with client(handler) as backend:
        assert (await backend.submit("custom", {"内容": "原请求"}, "persisted"))[
            "run_id"
        ] == "run_original"
    assert len(exchanges) == 2 and len(sent) == 3
    assert len({r.content for r, _ in sent}) == 1
    assert len({c.nonce for _, c in sent}) == 3
    assert all(c.request.idempotency_key == "persisted" for _, c in sent)


async def test_non_idempotent_creation_is_not_retried_and_foreign_urls_never_sent():
    sent = []

    def handler(request):
        sent.append(request)
        if request.url.path == "/api/v1/auth/token":
            return httpx.Response(200, json={"access_token": "opaque", "expires_in": 60})
        raise httpx.ReadError("响应丢失")

    async with client(handler) as backend:
        with pytest.raises(ValueError):
            await backend.request("GET", "https://another.example/api/v1/runs/run_a")
        assert not sent
        with pytest.raises(httpx.ReadError):
            await backend.create_conversation("custom", "会话")
    assert len(sent) == 2


def frame(sequence, kind="text_delta"):
    return (
        f"id: {sequence}\nevent: {kind}\ndata: "
        + json.dumps(
            {
                "run_id": "run_original",
                "sequence": sequence,
                "event_type": kind,
                "payload": {"validated": False} if kind == "text_delta" else {},
            }
        )
        + "\n\n"
    )


async def test_stream_control_refresh_and_cursor_deduplication():
    subscriptions, exchanges = [], []

    def handler(request):
        if request.url.path == "/api/v1/auth/token":
            exchanges.append(1)
            return httpx.Response(
                200, json={"access_token": f"opaque-{len(exchanges)}", "expires_in": 60}
            )
        subscriptions.append(claims(request))
        if len(subscriptions) == 1:
            text = (
                ": heartbeat\n\n"
                + frame(1)
                + 'event: control\ndata: {"status":401,"code":"AUTH_EXPIRED","message":"到期"}\n\n'
            )
        else:
            text = frame(1) + frame(2, "result") + frame(3, "completed")
        return httpx.Response(200, text=text, headers={"Content-Type": "text/event-stream"})

    async with client(handler) as backend:
        events = [event async for event in backend.subscribe("run_original")]
    assert [e.sequence for e in events] == [1, 2, 3]
    assert events[0].data["payload"]["validated"] is False
    assert subscriptions[1].request.target.endswith("after_sequence=1")
    assert subscriptions[0].nonce != subscriptions[1].nonce
    assert len(exchanges) == 2


@pytest.mark.parametrize("code,status", [("EVENTS_EXPIRED", 410), ("FORBIDDEN", 403)])
async def test_expired_events_query_original_but_revoked_access_stops(code, status):
    calls = []

    def handler(request):
        calls.append(request.url.path)
        if request.url.path == "/api/v1/auth/token":
            return httpx.Response(200, json={"access_token": "opaque", "expires_in": 60})
        claims(request)
        if request.url.path.endswith("events"):
            return error(status, code)
        return httpx.Response(
            200, json={"run_id": "run_original", "state": "SUCCEEDED", "result": {}}
        )

    async with client(handler) as backend:
        if status == 410:
            events = [e async for e in backend.subscribe("run_original")]
            assert events[0].type == "snapshot" and events[0].sequence is None
        else:
            with pytest.raises(PlatformError) as failure:
                _ = [e async for e in backend.subscribe("run_original")]
            assert failure.value.request_id == "request-test"
            assert len(calls) == 2


def test_backend_openapi_matches_real_routes_and_error_shapes():
    schema = build_schema(backend=True)
    assert all(p.startswith("/api/v1/") for p in schema["paths"])
    post = schema["paths"]["/api/v1/runs"]["post"]
    for status, name in [
        ("200", "ResultEnvelope"),
        ("202", "AdmissionReceipt"),
        ("422", "ErrorResponse"),
    ]:
        assert post["responses"][status]["content"]["application/json"]["schema"] == {
            "$ref": f"#/components/schemas/{name}"
        }
    events = schema["paths"]["/api/v1/runs/{run_id}/events"]["get"]
    assert {"Last-Event-ID", "after_sequence", "X-Business-Delegation"} <= {
        p["name"] for p in events["parameters"]
    }
    assert events["responses"]["410"]["content"]["application/json"]["schema"]["$ref"].endswith(
        "ErrorResponse"
    )
    assert "text/event-stream" in events["responses"]["200"]["content"]
    assert "/api/v1/conversations" in schema["paths"]
    assert "/api/v1/artifacts/{artifact_id}/content" in schema["paths"]
