"""SDK 使用真实协议响应验证 202、同键重试、错误信息及 SSE 游标。"""

import importlib.util
import json
import sys
from pathlib import Path

import httpx
import pytest

spec = importlib.util.spec_from_file_location(
    "creativity_sdk", Path("sdks/python/creativity_sdk/__init__.py")
)
sdk = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = sdk
spec.loader.exec_module(sdk)


async def test_python_sdk_preserves_key_and_error_and_reconnects():
    calls = []
    streams = 0
    signed = []

    def sign(request):
        signed.append(request)
        return {"X-Subject-Proof": "fixture"}

    async def handle(request):
        nonlocal streams
        calls.append(request)
        if request.url.path.endswith("/events"):
            streams += 1
            if streams == 1:
                return httpx.Response(200, text='id: 1\nevent: accepted\ndata: {"ok":true}\n\n')
            assert request.headers["Last-Event-ID"] == "1"
            return httpx.Response(200, text='id: 2\nevent: completed\ndata: {"state":"FAILED"}\n\n')
        if request.method == "GET":
            return httpx.Response(200, json={"state": "RUNNING"})
        if request.url.path.endswith("/cancel"):
            return httpx.Response(
                403,
                json={
                    "error": {"code": "FORBIDDEN", "message": "授权失效"},
                    "request_id": "request-fixture",
                },
            )
        if len(calls) == 1:
            return httpx.Response(503, json={})
        return httpx.Response(202, json={"run_id": "run-one", "state": "QUEUED"})

    async with sdk.Client(
        "https://platform.test", "token", headers=sign, transport=httpx.MockTransport(handle)
    ) as client:
        result = await client.create_run("example", {}, idempotency_key="stable-key")
        assert result.status == 202
        assert (
            calls[0].headers["Idempotency-Key"]
            == calls[1].headers["Idempotency-Key"]
            == "stable-key"
        )
        assert calls[0].content == calls[1].content == signed[0].body
        assert signed[0].method == "POST" and signed[0].path == "/api/v1/runs"
        assert signed[0].idempotency_key == "stable-key"
        events = [event async for event in client.events("run-one")]
        assert [event.sequence for event in events] == [1, 2]
        assert events[-1].data["state"] == "FAILED"
        with pytest.raises(sdk.ApiError) as error:
            await client.cancel("run-one")
        assert (error.value.status, error.value.code, error.value.request_id) == (
            403,
            "FORBIDDEN",
            "request-fixture",
        )


def event_frame(sequence, kind, payload):
    data = {"sequence": sequence, "event_type": kind, "payload": payload}
    return f"id: {sequence}\nevent: {kind}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


@pytest.mark.parametrize("state", ["SUCCEEDED", "FAILED", "CANCELLED", "TIMED_OUT"])
@pytest.mark.parametrize("received", [1, 2])
async def test_python_sdk_replays_terminal_events_after_early_eof(state, received):
    cursors = []
    kind = "result" if state == "SUCCEEDED" else "error"
    payload = {"data": {"answer": "完整结果"}} if kind == "result" else {"code": state}
    frames = [
        event_frame(1, "text_delta", {"text": "部分内容"}),
        event_frame(2, kind, payload),
        event_frame(3, "completed", {"state": state}),
    ]

    async def handle(request):
        if request.url.path.endswith("/events"):
            cursors.append(request.headers["Last-Event-ID"])
            if len(cursors) == 1:
                # 下一帧只收到一部分，必须从最后完整交付的游标恢复。
                return httpx.Response(200, text="".join(frames[:received]) + frames[received][:12])
            # 重放最后已交付事件，验证结果和错误不会重复交付。
            return httpx.Response(200, text="".join(frames[received - 1 :]))
        return httpx.Response(200, json={"state": state, kind: payload})

    async with sdk.Client("https://platform.test", transport=httpx.MockTransport(handle)) as client:
        events = [event async for event in client.events("run-one", reconnects=1)]

    assert cursors == ["0", str(received)]
    assert [event.sequence for event in events] == [1, 2, 3]
    assert [event.event for event in events] == ["text_delta", kind, "completed"]
    assert events[1].data["payload"] == payload
    assert events[2].data["payload"] == {"state": state}


@pytest.mark.parametrize("reconnects", [0, 1])
async def test_python_sdk_reports_incomplete_delivery_after_reconnect_limit(reconnects):
    cursors = []
    received = []

    async def handle(request):
        if request.url.path.endswith("/events"):
            cursors.append(request.headers["Last-Event-ID"])
            return httpx.Response(200, text=event_frame(1, "result", {"data": "完整结果"}))
        return httpx.Response(200, json={"state": "SUCCEEDED", "result": {"data": "完整结果"}})

    async with sdk.Client("https://platform.test", transport=httpx.MockTransport(handle)) as client:
        with pytest.raises(sdk.ApiError) as error:
            async for event in client.events("run-one", reconnects=reconnects):
                received.append(event)

    assert (error.value.status, error.value.code) == (503, "STREAM_INTERRUPTED")
    assert cursors == ["0"] + ["1"] * reconnects
    assert [event.event for event in received] == ["result"]


@pytest.mark.parametrize("failure", ["expired", "control"])
async def test_python_sdk_propagates_replay_errors_for_terminal_run(failure):
    cursors = []

    async def handle(request):
        if request.url.path.endswith("/events"):
            cursors.append(request.headers["Last-Event-ID"])
            if len(cursors) == 1:
                return httpx.Response(200, text=event_frame(1, "text_delta", {"text": "部分内容"}))
            if failure == "expired":
                return httpx.Response(
                    410,
                    json={
                        "error": {"code": "EVENTS_EXPIRED", "message": "事件已过期"},
                        "request_id": "request-expired",
                    },
                )
            return httpx.Response(
                200, text='event: control\ndata: {"status":401,"code":"AUTH_EXPIRED"}\n\n'
            )
        return httpx.Response(200, json={"state": "SUCCEEDED"})

    async with sdk.Client("https://platform.test", transport=httpx.MockTransport(handle)) as client:
        with pytest.raises(sdk.ApiError) as error:
            _ = [event async for event in client.events("run-one")]

    assert cursors == ["0", "1"]
    assert (error.value.status, error.value.code, error.value.request_id) == (
        (410, "EVENTS_EXPIRED", "request-expired")
        if failure == "expired"
        else (401, "AUTH_EXPIRED", None)
    )
