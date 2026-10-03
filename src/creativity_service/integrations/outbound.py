"""固定目的地的有界 HTTP 传输，身份交换与回调投递复用相同出站边界。"""

import asyncio
import json
from dataclasses import dataclass
from typing import Any

import httpcore

from creativity_service.core.context import Scope
from creativity_service.core.database import assert_external_io_allowed
from creativity_service.core.primitives import ServiceError
from creativity_service.core.security.outbound import OutboundPolicy, Purpose
from creativity_service.integrations.tools.http import PinnedBackend


@dataclass(frozen=True)
class HttpResponse:
    status: int
    body: bytes

    def json(self) -> dict[str, Any]:
        try:
            value = json.loads(self.body)
            if not isinstance(value, dict):
                raise ValueError()
            return value
        except (ValueError, UnicodeError):
            raise ServiceError("REMOTE_RESPONSE_INVALID", "来源响应不是 JSON 对象", 502) from None


class BoundedHttp:
    def __init__(self, policy: OutboundPolicy) -> None:
        self.policy = policy

    async def post(
        self,
        scope: Scope,
        purpose: Purpose,
        url: str,
        body: bytes,
        *,
        headers: dict[str, str] | None = None,
        maximum: int = 65536,
    ) -> HttpResponse:
        assert_external_io_allowed()
        target = await self.policy.validate(scope, purpose, url)
        actual = {
            "Content-Type": "application/json",
            "Accept": "application/json",
            "Accept-Encoding": "identity",
            **(headers or {}),
        }
        if any(any(ord(c) < 32 or ord(c) == 127 for c in str(v)) for v in actual.values()):
            raise ServiceError("OUTBOUND_HEADERS_INVALID", "出站请求头不正确", 422)
        try:
            async with asyncio.timeout(20):
                async with httpcore.AsyncConnectionPool(
                    network_backend=PinnedBackend(target), retries=0
                ) as pool:
                    async with pool.stream(
                        "POST", target.url, headers=list(actual.items()), content=body
                    ) as response:
                        values = {
                            k.decode().lower(): v.decode("latin1") for k, v in response.headers
                        }
                        if values.get("content-encoding", "identity") not in {"", "identity"}:
                            raise ServiceError("REMOTE_RESPONSE_INVALID", "响应不能压缩", 502)
                        size = values.get("content-length")
                        if size and (not size.isdigit() or int(size) > maximum):
                            raise ServiceError("REMOTE_RESPONSE_LIMIT", "来源响应超过大小上限", 502)
                        result = bytearray()
                        async for block in response.aiter_stream():
                            result.extend(block)
                            if len(result) > maximum:
                                raise ServiceError(
                                    "REMOTE_RESPONSE_LIMIT", "来源响应超过大小上限", 502
                                )
                        return HttpResponse(response.status, bytes(result))
        except (
            OSError,
            TimeoutError,
            httpcore.NetworkError,
            httpcore.ProtocolError,
            httpcore.TimeoutException,
        ):
            raise ServiceError("REMOTE_UNAVAILABLE", "来源连接失败或响应超时", 503) from None
