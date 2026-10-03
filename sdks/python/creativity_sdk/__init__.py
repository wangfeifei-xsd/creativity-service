"""Creativity 业务端异步客户端，重试保留原幂等键与原运行。"""

import asyncio
import json
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass
from typing import Any
from urllib.parse import quote

import httpx


class ApiError(Exception):
    def __init__(self, status: int, code: str, message: str, request_id: str | None) -> None:
        super().__init__(message)
        self.status, self.code, self.request_id = status, code, request_id


@dataclass(frozen=True)
class RunResponse:
    status: int
    data: dict[str, Any]


@dataclass(frozen=True)
class Event:
    sequence: int | None
    event: str
    data: dict[str, Any]


@dataclass(frozen=True)
class RequestContext:
    method: str
    path: str
    body: bytes
    idempotency_key: str | None


class Client:
    def __init__(
        self,
        base_url: str,
        token: str | None = None,
        *,
        headers: Callable[[RequestContext], dict[str, str]] | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.token, self.headers = token, headers or (lambda request: {})
        self.http = httpx.AsyncClient(
            base_url=base_url.rstrip("/"),
            transport=transport,
            timeout=30,
            follow_redirects=False,
            trust_env=False,
        )

    async def __aenter__(self) -> "Client":
        return self

    async def __aexit__(self, *_: Any) -> None:
        await self.http.aclose()

    def request_headers(self, request: RequestContext) -> dict[str, str]:
        headers = dict(self.headers(request))
        if self.token:
            headers["Authorization"] = "Bearer " + self.token
        return headers

    @staticmethod
    def check(response: httpx.Response) -> None:
        if 200 <= response.status_code < 300:
            return
        try:
            body = response.json()
        except ValueError:
            body = {}
        error = body.get("error", {})
        raise ApiError(
            response.status_code,
            error.get("code", "HTTP_ERROR"),
            error.get("message", "平台请求失败"),
            body.get("request_id") or response.headers.get("X-Request-ID"),
        )

    async def request(
        self,
        method: str,
        path: str,
        body: dict[str, Any] | None = None,
        *,
        key: str | None = None,
        retries: int = 0,
    ) -> RunResponse:
        if retries and method != "GET" and not key:
            raise ValueError("重试写请求必须沿用幂等键")
        if not 0 <= retries <= 5:
            raise ValueError("重试次数须在零至五次之间")
        payload = (
            json.dumps(body, ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode()
            if body is not None
            else b""
        )
        request = RequestContext(method, path, payload, key)
        for attempt in range(retries + 1):
            headers = self.request_headers(request)
            if body is not None:
                headers["Content-Type"] = "application/json"
            if key:
                headers["Idempotency-Key"] = key
            try:
                response = await self.http.request(method, path, content=payload, headers=headers)
                if response.status_code in {502, 503, 504} and attempt < retries:
                    await asyncio.sleep(min(2, 0.2 * 2**attempt))
                    continue
                self.check(response)
                return RunResponse(response.status_code, response.json())
            except httpx.TransportError:
                if attempt == retries:
                    raise
                await asyncio.sleep(min(2, 0.2 * 2**attempt))
        raise AssertionError("请求重试分支未返回")

    async def exchange(self, credential: dict[str, Any]) -> dict[str, Any]:
        response = await self.request("POST", "/api/v1/auth/token", credential)
        self.token = response.data["access_token"]
        return response.data

    async def create_run(
        self,
        agent_code: str,
        input: dict[str, Any],
        *,
        idempotency_key: str,
        delivery: str = "sync",
    ) -> RunResponse:
        if not idempotency_key or len(idempotency_key) > 128:
            raise ValueError("请提供稳定幂等键")
        return await self.request(
            "POST",
            "/api/v1/runs",
            {"agent_code": agent_code, "input": input, "delivery": delivery},
            key=idempotency_key,
            retries=2,
        )

    async def run(self, run_id: str) -> dict[str, Any]:
        return (await self.request("GET", "/api/v1/runs/" + quote(run_id, safe=""), retries=2)).data

    async def cancel(self, run_id: str) -> dict[str, Any]:
        return (
            await self.request("POST", "/api/v1/runs/" + quote(run_id, safe="") + "/cancel")
        ).data

    async def batch(self, body: dict[str, Any], *, idempotency_key: str) -> RunResponse:
        return await self.request("POST", "/api/v1/batches", body, key=idempotency_key, retries=2)

    async def events(
        self, run_id: str, *, after: int = 0, reconnects: int = 3
    ) -> AsyncIterator[Event]:
        if after < 0 or not 0 <= reconnects <= 20:
            raise ValueError("游标或重连次数不正确")
        cursor = after
        path = "/api/v1/runs/" + quote(run_id, safe="") + "/events"
        for attempt in range(reconnects + 1):
            headers = {
                **self.request_headers(RequestContext("GET", path, b"", None)),
                "Last-Event-ID": str(cursor),
                "Accept": "text/event-stream",
            }
            try:
                async with self.http.stream("GET", path, headers=headers) as response:
                    if response.status_code != 200:
                        await response.aread()
                        self.check(response)
                    event, sequence, data, size = "message", None, [], 0
                    async for line in response.aiter_lines():
                        size += len(line.encode())
                        if size > 2097152:
                            raise ApiError(502, "EVENT_TOO_LARGE", "事件超过体积上限", None)
                        if not line:
                            if data:
                                value = json.loads("\n".join(data))
                                if event == "control":
                                    raise ApiError(
                                        value.get("status", 403),
                                        value.get("code", "STREAM_STOPPED"),
                                        value.get("message", "事件流已停止"),
                                        None,
                                    )
                                if sequence is None or sequence > cursor:
                                    if sequence is not None:
                                        cursor = sequence
                                    yield Event(sequence, event, value)
                                if event == "completed":
                                    return
                            event, sequence, data, size = "message", None, [], 0
                        elif line.startswith("id:"):
                            sequence = int(line[3:].strip())
                        elif line.startswith("event:"):
                            event = line[6:].strip()
                        elif line.startswith("data:"):
                            data.append(line[5:].lstrip(" "))
                # 运行终态不能证明最终事件已交付；未收到 completed 时按已交付游标重连。
            except httpx.TransportError:
                if attempt == reconnects:
                    raise
            if attempt < reconnects:
                await asyncio.sleep(min(2, 0.2 * 2**attempt))
        raise ApiError(503, "STREAM_INTERRUPTED", "事件连接已中断，可使用最后游标恢复", None)
