"""可复制到任意业务后端的统一客户端；仅依赖 httpx，不导入平台或业务工程。"""

import asyncio
import base64
import hashlib
import hmac
import json
import re
import secrets
import time
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Any, Protocol
from urllib.parse import urlencode

import httpx

TERMINAL = {"SUCCEEDED", "FAILED", "CANCELLED", "TIMED_OUT"}


@dataclass(frozen=True)
class Principal:
    """只从业务后端登录会话及当前权限服务取得，不能直接采信浏览器正文。"""

    subject_type: str
    subject_id: str
    actions: list[str]
    resources: dict[str, list[str]]


class PrincipalService(Protocol):
    async def current(self) -> Principal:
        """每次发送前读取当前主体与权限；主体失效时应抛错。"""
        ...


class PlatformError(Exception):
    def __init__(self, status: int, code: str, message: str, request_id: str = "") -> None:
        self.status, self.code, self.message, self.request_id = status, code, message, request_id
        super().__init__(f"{status} {code}: {message}（请求标识：{request_id or '未提供'}）")


class WaitTimeout(TimeoutError):
    def __init__(self, run_id: str) -> None:
        self.run_id = run_id
        super().__init__(f"等待窗口结束，请继续查询原运行：{run_id}")


@dataclass(frozen=True)
class Event:
    type: str
    data: dict[str, Any]
    sequence: int | None = None


def encode_json(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode()


def sign_claims(claims: dict[str, Any], kid: str, secret: bytes) -> str:
    """签名覆盖实际载荷字节；与平台公开跨语言向量兼容。"""
    if len(secret) < 32 or not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", kid):
        raise ValueError("委托密钥及编号不符合协议")

    def b64(value: bytes) -> str:
        return base64.urlsafe_b64encode(value).decode("ascii").rstrip("=")

    payload = b64(encode_json(claims))
    signature = b64(
        hmac.digest(secret, f"business-delegation-v2\n{kid}\n{payload}".encode(), "sha256")
    )
    return f"v2.{kid}.{payload}.{signature}"


async def check_response(response: httpx.Response) -> None:
    if response.is_success:
        return
    await response.aread()
    try:
        value = response.json()
        error = value["error"]
        raise PlatformError(
            response.status_code,
            error["code"],
            error["message"],
            value.get("request_id") or response.headers.get("X-Request-ID", ""),
        )
    except (ValueError, KeyError, TypeError):
        # 网关可能返回 HTML，不能将原文或请求凭据写入排障输出。
        raise PlatformError(
            response.status_code,
            "HTTP_ERROR",
            "服务未返回标准错误，请按请求标识排查",
            response.headers.get("X-Request-ID", ""),
        ) from None


class BusinessBackendClient:
    def __init__(
        self,
        base_url: str,
        api_key: str,
        kid: str,
        signing_secret: bytes,
        issuer: str,
        audience: str,
        principals: PrincipalService,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        url = httpx.URL(base_url)
        if (
            url.scheme not in {"http", "https"}
            or not url.host
            or url.path not in {"", "/"}
            or url.query
            or url.fragment
            or url.userinfo
        ):
            raise ValueError("平台地址须为无凭据、路径、查询串的 HTTP(S) 源站")
        self.http = httpx.AsyncClient(
            base_url=base_url,
            timeout=httpx.Timeout(20, connect=5),
            follow_redirects=False,
            transport=transport,
        )
        self.api_key, self.kid, self.signing_secret = api_key, kid, signing_secret
        self.issuer, self.audience, self.principals = issuer, audience, principals
        self.token: str | None = None
        self.refresh_at = 0.0
        self.token_lock = asyncio.Lock()

    async def __aenter__(self) -> "BusinessBackendClient":
        return self

    async def __aexit__(self, *_: object) -> None:
        await self.close()

    async def close(self) -> None:
        await self.http.aclose()

    async def access_token(self) -> str:
        async with self.token_lock:
            if self.token and time.monotonic() < self.refresh_at:
                return self.token
            response = await self.http.post("/api/v1/auth/token", json={"api_key": self.api_key})
            await check_response(response)
            value = response.json()
            self.token = value["access_token"]
            self.refresh_at = time.monotonic() + max(0, value["expires_in"] - 5)
            assert self.token is not None
            return self.token

    def invalidate_token(self, authorization: str) -> None:
        # 并发请求不能用旧 401 清除另一请求刚换取的新 Token。
        if self.token and authorization == "Bearer " + self.token:
            self.token = None

    def request_target(self, target: str) -> str:
        if not target.startswith("/api/v1/") or "#" in target or "\\" in target:
            raise ValueError("只允许平台同源 /api/v1/ 路径")
        url = self.http.build_request("GET", target).url
        origin = self.http.base_url
        if (url.scheme, url.host, url.port) != (origin.scheme, origin.host, origin.port):
            raise ValueError("不能将凭据发送到其他源站")
        # httpx 会编码路径；签署最终发送的 ASCII 路径和查询串。
        return url.raw_path.decode("ascii")

    async def prepare(
        self, method: str, target: str, body: bytes, idempotency_key: str | None
    ) -> dict[str, str]:
        target = self.request_target(target)
        token = await self.access_token()
        principal = await self.principals.current()
        now = int(time.time())
        binding: dict[str, Any] = {
            "method": method.upper(),
            "target": target,
            "body_sha256": hashlib.sha256(body).hexdigest(),
        }
        if idempotency_key is not None:
            binding["idempotency_key"] = idempotency_key
        claims = {
            "subject_type": principal.subject_type,
            "subject_id": principal.subject_id,
            "actions": principal.actions,
            "resources": principal.resources,
            "issuer": self.issuer,
            "audience": self.audience,
            "issued_at": now,
            "expires_at": now + 300,
            "nonce": secrets.token_urlsafe(24),
            "request": binding,
        }
        headers = {
            "Authorization": "Bearer " + token,
            "X-Business-Delegation": sign_claims(claims, self.kid, self.signing_secret),
        }
        if body:
            headers["Content-Type"] = "application/json"
        if idempotency_key is not None:
            headers["Idempotency-Key"] = idempotency_key
        return headers

    async def response(
        self,
        method: str,
        target: str,
        value: dict[str, Any] | None = None,
        idempotency_key: str | None = None,
        *,
        retry_safe: bool = False,
    ) -> httpx.Response:
        target = self.request_target(target)
        body = encode_json(value) if value is not None else b""
        safe = (
            method == "GET"
            or retry_safe
            or (method == "POST" and target == "/api/v1/runs" and bool(idempotency_key))
        )
        refreshed, resent = False, False
        while True:
            headers = await self.prepare(method, target, body, idempotency_key)
            try:
                response = await self.http.request(method, target, content=body, headers=headers)
            except httpx.TransportError:
                if not safe or resent:
                    raise
                resent = True
                continue
            if response.status_code == 401 and not refreshed:
                self.invalidate_token(headers["Authorization"])
                refreshed = True
                continue
            await check_response(response)
            return response

    async def request(
        self,
        method: str,
        target: str,
        value: dict[str, Any] | None = None,
        idempotency_key: str | None = None,
        *,
        retry_safe: bool = False,
    ) -> dict[str, Any]:
        response = await self.response(
            method, target, value, idempotency_key, retry_safe=retry_safe
        )
        return response.json()

    async def submit(
        self,
        agent_code: str,
        business_input: dict[str, Any],
        idempotency_key: str,
        delivery: str = "async",
    ) -> dict[str, Any]:
        if not 1 <= len(idempotency_key) <= 128:
            raise ValueError("请提供持久化的业务请求幂等键，长度为 1 至 128")
        return await self.request(
            "POST",
            "/api/v1/runs",
            {"agent_code": agent_code, "input": business_input, "delivery": delivery},
            idempotency_key,
        )

    @staticmethod
    def identifier(value: str) -> str:
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", value):
            raise ValueError("资源标识不符合协议")
        return value

    async def query(self, run_id: str) -> dict[str, Any]:
        return await self.request("GET", "/api/v1/runs/" + self.identifier(run_id))

    async def cancel(self, run_id: str) -> dict[str, Any]:
        return await self.request(
            "POST", f"/api/v1/runs/{self.identifier(run_id)}/cancel", retry_safe=True
        )

    async def wait(
        self, run_id: str, *, wait_seconds: float = 60, poll_interval: float = 0.5
    ) -> dict[str, Any]:
        if wait_seconds <= 0 or poll_interval <= 0:
            raise ValueError("等待时长与查询间隔须为正数")
        try:
            async with asyncio.timeout(wait_seconds):
                while True:
                    value = await self.query(run_id)
                    if value["state"] in TERMINAL:
                        return value
                    await asyncio.sleep(poll_interval)
        except TimeoutError:
            raise WaitTimeout(run_id) from None

    async def subscribe(
        self, run_id: str, after: int = 0, *, max_reconnects: int = 3
    ) -> AsyncIterator[Event]:
        if after < 0 or max_reconnects < 0:
            raise ValueError("事件游标与重连次数不能为负数")
        run_id = self.identifier(run_id)
        cursor, refreshed = after, False
        for reconnect in range(max_reconnects + 1):
            target = f"/api/v1/runs/{run_id}/events?" + urlencode({"after_sequence": cursor})
            headers = await self.prepare("GET", target, b"", None)
            headers["Accept"] = "text/event-stream"
            try:
                async with self.http.stream("GET", target, headers=headers, timeout=60) as response:
                    if response.status_code == 401 and not refreshed:
                        self.invalidate_token(headers["Authorization"])
                        refreshed = True
                        continue
                    await check_response(response)
                    async for event in parse_events(response):
                        if event.type == "control":
                            if event.data["status"] == 401 and not refreshed:
                                self.invalidate_token(headers["Authorization"])
                                refreshed = True
                                break
                            raise PlatformError(
                                event.data["status"],
                                event.data["code"],
                                event.data["message"],
                                response.headers.get("X-Request-ID", ""),
                            )
                        if event.sequence is None or event.data.get("run_id") != run_id:
                            raise ValueError("事件缺少序号或运行归属不符")
                        if event.sequence <= cursor:
                            continue
                        if event.sequence != cursor + 1:
                            yield Event("snapshot", await self.query(run_id))
                            return
                        cursor = event.sequence
                        yield event
                        if event.type == "completed":
                            return
                    else:
                        snapshot = await self.query(run_id)
                        if snapshot["state"] in TERMINAL:
                            yield Event("snapshot", snapshot)
                            return
            except PlatformError as exc:
                if exc.status == 410 and exc.code == "EVENTS_EXPIRED":
                    yield Event("snapshot", await self.query(run_id))
                    return
                raise
            except httpx.TransportError:
                if reconnect == max_reconnects:
                    raise
            if reconnect < max_reconnects:
                await asyncio.sleep(min(0.25 * (2**reconnect), 2))
        raise PlatformError(503, "STREAM_INTERRUPTED", "订阅中断，请用已保存游标重连或查询原运行")

    async def download(self, artifact_id: str) -> bytes:
        return (
            await self.response("GET", f"/api/v1/artifacts/{self.identifier(artifact_id)}/content")
        ).content

    async def create_conversation(self, agent_code: str, title: str) -> dict[str, Any]:
        return await self.request(
            "POST", "/api/v1/conversations", {"agent_code": agent_code, "title": title}
        )

    async def send_message(
        self,
        conversation_id: str,
        client_message_id: str,
        content: str,
        business_input: dict[str, Any],
    ) -> dict[str, Any]:
        return await self.request(
            "POST",
            f"/api/v1/conversations/{self.identifier(conversation_id)}/messages",
            {"client_message_id": client_message_id, "content": content, "input": business_input},
            retry_safe=True,
        )


async def parse_events(response: httpx.Response) -> AsyncIterator[Event]:
    """按空行组帧，忽略心跳；半帧断线不推进游标。"""
    kind, identifier, data = "message", None, []
    async for line in response.aiter_lines():
        if not line:
            if data:
                value = json.loads("\n".join(data))
                sequence = int(identifier) if identifier is not None else None
                if not isinstance(value, dict) or (
                    sequence is not None and value.get("sequence") != sequence
                ):
                    raise ValueError("事件内容与游标不符")
                yield Event(kind, value, sequence)
            kind, identifier, data = "message", None, []
        elif not line.startswith(":"):
            field, _, value = line.partition(":")
            value = value.removeprefix(" ")
            if field == "event":
                kind = value
            elif field == "id":
                if not re.fullmatch(r"[0-9]{1,20}", value):
                    raise ValueError("事件序号不符合协议")
                identifier = value
            elif field == "data":
                data.append(value)
