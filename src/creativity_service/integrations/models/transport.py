"""HTTP 传输固定已核准 IP，保留原域名 TLS 校验，并收集原始计量事件。"""

import json
from collections.abc import AsyncIterator
from typing import Any
from urllib.parse import urlsplit

import httpcore
import httpx
from httpcore._backends.auto import AutoBackend

from creativity_service.core.primitives import ServiceError
from creativity_service.core.security.outbound import ValidatedTarget


class PinnedBackend(httpcore.AsyncNetworkBackend):
    def __init__(self, target: ValidatedTarget) -> None:
        self.target, self.backend = target, AutoBackend()

    async def connect_tcp(  # noqa: ASYNC109 — 实现 httpcore 的固定传输接口
        self,
        host: str,
        port: int,
        timeout: float | None = None,  # noqa: ASYNC109 — httpcore 接口约定
        local_address: str | None = None,
        socket_options: Any = None,
    ) -> httpcore.AsyncNetworkStream:
        if host != self.target.hostname or port != self.target.port:
            raise ServiceError("DESTINATION_FORBIDDEN", "模型请求目标发生变化", 403)
        # 每次尝试只选择一个核准地址；连接重试仍由统一运行时登记新 Attempt。
        return await self.backend.connect_tcp(
            self.target.addresses[0],
            port,
            timeout=timeout,
            local_address=local_address,
            socket_options=socket_options,
        )

    async def connect_unix_socket(  # noqa: ASYNC109 — 实现 httpcore 的固定传输接口
        self,
        path: str,
        timeout: float | None = None,  # noqa: ASYNC109 — httpcore 接口约定
        socket_options: Any = None,  # noqa: ASYNC109 — httpcore 接口约定
    ) -> httpcore.AsyncNetworkStream:
        raise ServiceError("DESTINATION_FORBIDDEN", "模型传输不允许本地套接字", 403)

    async def sleep(self, seconds: float) -> None:
        await self.backend.sleep(seconds)


class RawCapture:
    def __init__(self) -> None:
        self.request_sent = False
        self.request_id: str | None = None
        self.usage: dict[str, Any] | None = None
        self.response_id: str | None = None

    def observe(self, payload: dict[str, Any]) -> None:
        body = payload.get("message", payload)
        if isinstance(body, dict):
            self.response_id = body.get("id") or self.response_id
            raw = body.get("usage")
            if isinstance(raw, dict):
                # Messages 的 message_start 和 message_delta 是同一请求的累积片段。
                self.usage = {**(self.usage or {}), **raw}


class CapturedStream(httpx.AsyncByteStream):
    def __init__(self, stream: httpx.AsyncByteStream, capture: RawCapture, sse: bool) -> None:
        self.stream, self.capture, self.sse = stream, capture, sse
        self.buffer = b""

    async def __aiter__(self) -> AsyncIterator[bytes]:
        async for chunk in self.stream:
            self.buffer += chunk
            if len(self.buffer) > 8 * 1024 * 1024:
                raise ServiceError("MODEL_RESPONSE_TOO_LARGE", "模型响应超出允许范围", 502)
            if self.sse:
                while b"\n" in self.buffer:
                    line, self.buffer = self.buffer.split(b"\n", 1)
                    if line.startswith(b"data:"):
                        self.observe(line[5:].strip())
            yield chunk
        if self.buffer:
            self.observe(self.buffer)

    def observe(self, data: bytes) -> None:
        try:
            payload = json.loads(data)
            if isinstance(payload, dict):
                self.capture.observe(payload)
        except (ValueError, UnicodeError):
            pass

    async def aclose(self) -> None:
        await self.stream.aclose()


class ModelTransport(httpx.AsyncBaseTransport):
    def __init__(
        self,
        target: ValidatedTarget,
        capture: RawCapture,
        inner: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        if inner is None:
            transport = httpx.AsyncHTTPTransport(retries=0, trust_env=False)
            # HTTPX 0.28 的连接池扩展点；httpcore 保持原始 host 用于 SNI。
            transport._pool = httpcore.AsyncConnectionPool(
                network_backend=PinnedBackend(target), retries=0
            )
            inner = transport
        self.inner, self.target, self.capture = inner, target, capture
        self.sent = False

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        if self.sent:
            raise ServiceError("MODEL_ATTEMPT_REUSED", "一次模型尝试只能发送一个供应商请求", 409)
        base = urlsplit(self.target.url)
        if (
            request.url.host != self.target.hostname
            or request.url.scheme != base.scheme
            or (request.url.port or (443 if request.url.scheme == "https" else 80))
            != self.target.port
            or not request.url.path.startswith(base.path.rstrip("/") + "/")
        ):
            raise ServiceError("DESTINATION_FORBIDDEN", "模型请求目标发生变化", 403)
        self.sent = True
        self.capture.request_sent = True
        response = await self.inner.handle_async_request(request)
        if response.is_redirect:
            await response.aclose()
            raise ServiceError(
                "MODEL_REDIRECT_FORBIDDEN", "模型连接不接受重定向，请更新地址后重新验证", 403
            )
        self.capture.request_id = response.headers.get("x-request-id") or response.headers.get(
            "request-id"
        )
        if response.is_stream_consumed:
            try:
                payload = response.json()
                if isinstance(payload, dict):
                    self.capture.observe(payload)
            except ValueError:
                pass
        assert isinstance(response.stream, httpx.AsyncByteStream)
        response.stream = CapturedStream(
            response.stream,
            self.capture,
            "text/event-stream" in response.headers.get("content-type", ""),
        )
        return response

    async def aclose(self) -> None:
        await self.inner.aclose()
