"""SDK 使用真实协议响应验证 202、同键重试、错误信息及 SSE 游标。"""

import importlib.util
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
