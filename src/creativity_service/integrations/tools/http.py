"""固定目的地 HTTP 适配器：钉住核准 IP，保留 TLS 主机校验并有界读取。"""

import asyncio
import json
from collections.abc import Awaitable, Callable, Mapping
from typing import Any, Literal
from urllib.parse import urlencode, urlsplit, urlunsplit

import httpcore
from httpcore._backends.auto import AutoBackend
from pydantic import ValidationError

from creativity_service.core.context import AuthContext
from creativity_service.core.database import assert_external_io_allowed
from creativity_service.core.primitives import canonical_json
from creativity_service.core.security.outbound import OutboundPolicy, ValidatedTarget
from creativity_service.integrations.tools import AdapterRequest, AdapterResult, ToolAdapterError

CredentialHeaders = Callable[[AuthContext], Awaitable[Mapping[str, str]]]


class PinnedBackend(httpcore.AsyncNetworkBackend):
    def __init__(self, target: ValidatedTarget) -> None:
        self.target, self.backend = target, AutoBackend()

    async def connect_tcp(  # noqa: ASYNC109 — 实现固定网络接口
        self,
        host: str,
        port: int,
        timeout: float | None = None,  # noqa: ASYNC109 — httpcore 接口要求
        local_address: str | None = None,
        socket_options: Any = None,
    ) -> httpcore.AsyncNetworkStream:
        if host != self.target.hostname or port != self.target.port:
            raise ToolAdapterError("TOOL_UNAVAILABLE", "工具请求目标发生变化")
        # 仅连接核准 IP；HTTP 库继续以原域名执行 TLS/SNI 校验，不自动重试。
        return await self.backend.connect_tcp(
            self.target.addresses[0],
            port,
            timeout=timeout,
            local_address=local_address,
            socket_options=socket_options,
        )

    async def connect_unix_socket(  # noqa: ASYNC109 — 实现固定网络接口
        self,
        path: str,
        timeout: float | None = None,  # noqa: ASYNC109 — httpcore 接口要求
        socket_options: Any = None,
    ) -> httpcore.AsyncNetworkStream:
        raise ToolAdapterError("TOOL_UNAVAILABLE", "工具不允许访问本地套接字")

    async def sleep(self, seconds: float) -> None:
        await self.backend.sleep(seconds)


class HttpToolAdapter:
    def __init__(
        self,
        url: str,
        method: Literal["GET", "POST"],
        policy: OutboundPolicy,
        credentials: CredentialHeaders | None = None,
    ) -> None:
        self.url, self.method, self.policy, self.credentials = url, method, policy, credentials

    async def invoke(self, request: AdapterRequest) -> AdapterResult:
        assert_external_io_allowed()
        target = await self.policy.validate(request.context.scope, "http_tool", self.url)
        headers = {
            "Accept": "application/json",
            "Content-Type": "application/json",
            "Accept-Encoding": "identity",
            "X-Request-ID": request.attempt_id,
        }
        if request.operation:
            headers["Idempotency-Key"] = request.operation["idempotency_key"]
        if self.credentials is not None:
            secrets = dict(await self.credentials(request.context))
            if set(secrets) != {"Authorization"} or any(
                ord(c) < 32 or ord(c) == 127 for c in secrets.get("Authorization", "")
            ):
                raise ToolAdapterError("TOOL_UNAVAILABLE", "来源认证头配置不正确")
            headers.update(secrets)
        identity = {
            **request.context.scope.model_dump(),
            "principal_id": request.context.principal_id,
            "client_id": request.context.client_id,
            "key_id": request.context.key_id,
        }
        if request.operation:
            identity["operation"] = request.operation
        body = canonical_json({"arguments": request.arguments, "identity": identity})
        parts = urlsplit(target.url)
        path = urlunsplit(("", "", parts.path or "/", parts.query, ""))
        if self.method == "GET":
            if any(isinstance(v, (dict, list)) for v in request.arguments.values()):
                raise ToolAdapterError("TOOL_INPUT_INVALID", "此查询接口仅接受标量参数")
            query = urlencode(request.arguments)
            path += ("&" if parts.query else "?") + query
            headers["X-Creativity-Identity"] = json.dumps(identity, ensure_ascii=True)
        try:
            async with asyncio.timeout(request.definition.timeout_seconds):
                status, content_type, encoding, source_id, data = await self._send(
                    target, request, path, headers, body if self.method == "POST" else None
                )
        except (TimeoutError, httpcore.TimeoutException):
            raise ToolAdapterError("TOOL_TIMEOUT", "工具来源响应超时", retryable=True) from None
        except (OSError, httpcore.NetworkError, httpcore.ProtocolError):
            raise ToolAdapterError("TOOL_UNAVAILABLE", "工具来源连接失败", retryable=True) from None
        if status in {429, 502, 503, 504}:
            raise ToolAdapterError(
                "TOOL_UNAVAILABLE", "工具来源暂不可用", retryable=True, source_request_id=source_id
            )
        if status < 200 or status >= 300:
            raise ToolAdapterError(
                "TOOL_UNAVAILABLE", "工具来源拒绝请求或返回重定向", source_request_id=source_id
            )
        if content_type != "application/json" or encoding not in {"", "identity"}:
            raise ToolAdapterError("TOOL_RESULT_INVALID", "来源须返回未压缩的 JSON 结果")
        try:
            return AdapterResult.model_validate_json(data)
        except (ValidationError, ValueError):
            raise ToolAdapterError(
                "TOOL_RESULT_INVALID", "工具来源结果格式不正确", source_request_id=source_id
            ) from None

    async def _send(
        self,
        target: ValidatedTarget,
        request: AdapterRequest,
        path: str,
        headers: dict[str, str],
        body: bytes | None,
    ) -> tuple[int, str, str, str | None, bytes]:
        parts = urlsplit(target.url)
        url = urlunsplit(
            (
                parts.scheme,
                parts.netloc,
                path.split("?", 1)[0],
                path.split("?", 1)[1] if "?" in path else "",
                "",
            )
        )
        async with httpcore.AsyncConnectionPool(
            network_backend=PinnedBackend(target), retries=0
        ) as pool:
            async with pool.stream(
                self.method, url, headers=list(headers.items()), content=body
            ) as response:
                received = {
                    k.decode("ascii").lower(): v.decode("latin1") for k, v in response.headers
                }
                maximum = request.definition.max_result_size
                length = received.get("content-length")
                if length and (not length.isdigit() or int(length) > maximum):
                    raise ToolAdapterError("TOOL_RESULT_INVALID", "工具结果超过体积上限")
                data = bytearray()
                async for chunk in response.aiter_stream():
                    if len(data) + len(chunk) > maximum:
                        raise ToolAdapterError("TOOL_RESULT_INVALID", "工具结果超过体积上限")
                    data.extend(chunk)
                source_id = received.get("x-request-id")
                if source_id and len(source_id) > 256:
                    source_id = None
                return (
                    response.status,
                    received.get("content-type", "").split(";", 1)[0].lower(),
                    received.get("content-encoding", ""),
                    source_id,
                    bytes(data),
                )
