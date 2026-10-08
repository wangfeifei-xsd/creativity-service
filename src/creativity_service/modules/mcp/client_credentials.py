"""以应用凭据换取短期令牌；缓存按渠道、环境、连接及凭据修订隔离。"""

import asyncio
from collections import OrderedDict
from collections.abc import Awaitable, Callable
from time import monotonic
from typing import Any
from urllib.parse import urlencode

from pydantic import SecretBytes

from creativity_service.core.context import AuthContext
from creativity_service.core.primitives import ServiceError
from creativity_service.core.security.outbound import OutboundPolicy
from creativity_service.integrations.outbound import BoundedHttp


class ClientCredentials:
    def __init__(self, outbound: OutboundPolicy, http: BoundedHttp | None = None) -> None:
        self.http = http or BoundedHttp(outbound)
        self.cache: OrderedDict[tuple[Any, ...], tuple[SecretBytes, float]] = OrderedDict()
        self.pending: dict[tuple[Any, ...], asyncio.Task[tuple[SecretBytes, float]]] = {}

    async def exchange(
        self, context: AuthContext, row: dict[str, Any], secret: SecretBytes
    ) -> tuple[SecretBytes, float]:
        config = row["authentication"]
        started = monotonic()
        response = await self.http.post(
            context.scope,
            "oauth",
            config["token_endpoint"],
            urlencode(
                {
                    "grant_type": "client_credentials",
                    "client_id": config["app_id"],
                    "client_secret": secret.get_secret_value().decode(),
                    "resource": row["endpoint"],
                    "scope": "mcp",
                }
            ).encode(),
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            maximum=16384,
        )
        if response.status in {400, 401, 403}:
            raise ServiceError("MCP_AUTH_FAILED", "应用鉴权失败，请检查应用状态与凭据", 403)
        if response.status != 200:
            raise ServiceError("MCP_UNAVAILABLE", "令牌服务暂不可用", 502)
        value = response.json()
        token, seconds = value.get("access_token"), value.get("expires_in")
        if (
            not isinstance(token, str)
            or not 1 <= len(token) <= 8192
            or any(ord(c) < 33 or ord(c) > 126 for c in token)
            or str(value.get("token_type", "")).lower() != "bearer"
            or type(seconds) is not int
            or not 1 <= seconds <= 86400
            or ("scope" in value and value["scope"] != "mcp")
        ):
            raise ServiceError("MCP_AUTH_FAILED", "令牌服务返回的凭据或有效期不正确", 502)
        return SecretBytes(token.encode()), started + seconds

    async def call[T](
        self,
        context: AuthContext,
        row: dict[str, Any],
        secret: SecretBytes,
        operation: Callable[[SecretBytes | None], Awaitable[T]],
    ) -> T:
        key = (
            context.scope.channel_id,
            context.scope.environment,
            row["id"],
            row["configuration_revision"],
            row["credential_ref"],
            row["credential_revision"],
        )
        margin = max(30, row["timeouts"]["operation_seconds"] + 5)
        cached = self.cache.get(key)
        if cached is None or cached[1] <= monotonic() + margin:
            task = self.pending.get(key)
            if task is None:
                if len(self.pending) >= 256:
                    raise ServiceError("MCP_UNAVAILABLE", "鉴权请求繁忙，请稍后重试", 503)
                task = asyncio.create_task(self.exchange(context, row, secret))
                self.pending[key] = task

                def completed(done: asyncio.Task[tuple[SecretBytes, float]]) -> None:
                    if self.pending.get(key) is done:
                        self.pending.pop(key, None)
                    if not done.cancelled():
                        done.exception()

                task.add_done_callback(completed)
            try:
                cached = await asyncio.shield(task)
            finally:
                if task.done() and self.pending.get(key) is task:
                    self.pending.pop(key, None)
            self.cache[key] = cached
            self.cache.move_to_end(key)
            while len(self.cache) > 256:
                self.cache.popitem(last=False)
        try:
            return await operation(cached[0])
        except ServiceError as failure:
            if failure.code == "MCP_AUTH_FAILED":
                self.cache.pop(key, None)
                raise ServiceError(
                    "MCP_ACCESS_TOKEN_REJECTED", "访问令牌已失效，下次调用将重新鉴权", 403
                ) from None
            # 令牌失效不自动重放已经提交的业务操作。
            raise
