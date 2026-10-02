"""业务后端示例：缓存服务 Token、服务端取主体、绑定请求并有界重试。"""

import asyncio
import secrets
import time
from collections.abc import AsyncIterator
from typing import Any, Protocol
from urllib.parse import urlencode

import httpx

from creativity_service.core.primitives import canonical_json
from creativity_service.integrations.business.delegation import (
    DelegationClaims,
    SourceScope,
    bind_request,
    sign,
)


class AuthorizedPrincipal(Protocol):
    """只能由源业务登录会话与当前权限服务构造，不能反序列化前端 user/club 字段。"""

    subject_type: str
    subject_id: str
    source_scope: SourceScope
    actions: list[str]
    resources: dict[str, list[str]]


class PrincipalService(Protocol):
    async def current(self) -> AuthorizedPrincipal:
        """从业务后端当前请求会话取得登录主体、数据域和实时权限。"""
        ...


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
    ) -> None:
        self.http = httpx.AsyncClient(base_url=base_url, timeout=20, follow_redirects=False)
        self.api_key, self.kid, self.signing_secret = api_key, kid, signing_secret
        self.issuer, self.audience, self.principals = issuer, audience, principals
        self.token: str | None = None
        self.refresh_at = 0.0
        self.token_lock = asyncio.Lock()

    async def access_token(self) -> str:
        async with self.token_lock:
            if self.token and time.monotonic() < self.refresh_at:
                return self.token
            response = await self.http.post("/api/v1/auth/token", json={"api_key": self.api_key})
            response.raise_for_status()
            value = response.json()
            self.token = value["access_token"]
            self.refresh_at = time.monotonic() + max(0, value["expires_in"] - 5)
            assert self.token is not None
            return self.token

    async def prepare(
        self, method: str, target: str, body: bytes, idempotency_key: str | None
    ) -> dict[str, str]:
        principal = await self.principals.current()
        now = int(time.time())
        claims = DelegationClaims(
            subject_type=principal.subject_type,
            subject_id=principal.subject_id,
            data_scope=principal.source_scope,
            actions=principal.actions,
            resources=principal.resources,
            issuer=self.issuer,
            audience=self.audience,
            issued_at=now,
            expires_at=now + 300,
            nonce=secrets.token_urlsafe(24),
            request=bind_request(method, target, body, idempotency_key),
        )
        headers = {
            "Authorization": "Bearer " + await self.access_token(),
            "X-Business-Delegation": sign(claims, self.kid, self.signing_secret),
        }
        if body:
            headers["Content-Type"] = "application/json"
        if idempotency_key:
            headers["Idempotency-Key"] = idempotency_key
        return headers

    async def request(
        self,
        method: str,
        target: str,
        value: dict[str, Any] | None = None,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        body = canonical_json(value) if value is not None else b""
        headers = await self.prepare(method, target, body, idempotency_key)
        # 断网重发复用原正文、委托和幂等键；401 只换一次 Token，不在浏览器持有凭据。
        for attempt in range(2):
            try:
                response = await self.http.request(method, target, content=body, headers=headers)
            except httpx.TransportError:
                if attempt:
                    raise
                continue
            if response.status_code == 401 and attempt == 0:
                self.token = None
                headers["Authorization"] = "Bearer " + await self.access_token()
                continue
            response.raise_for_status()
            return response.json()
        raise RuntimeError("业务请求重试已结束")

    async def submit(
        self,
        agent_code: str,
        business_input: dict[str, Any],
        idempotency_key: str,
        delivery: str = "async",
    ) -> dict[str, Any]:
        return await self.request(
            "POST",
            "/api/v1/runs",
            {
                "agent_code": agent_code,
                "input": business_input,
                "delivery": delivery,
            },
            idempotency_key,
        )

    async def query(self, run_id: str) -> dict[str, Any]:
        return await self.request("GET", "/api/v1/runs/" + run_id)

    async def cancel(self, run_id: str) -> dict[str, Any]:
        return await self.request("POST", f"/api/v1/runs/{run_id}/cancel")

    async def subscribe(self, run_id: str, after: int = 0) -> AsyncIterator[str]:
        # 每次重连对实际游标路径重新签名；业务后端保存 last event id 再继续读取。
        target = f"/api/v1/runs/{run_id}/events?" + urlencode({"after": after})
        headers = await self.prepare("GET", target, b"", None)
        for attempt in range(2):
            async with self.http.stream("GET", target, headers=headers, timeout=60) as response:
                if response.status_code == 401 and attempt == 0:
                    self.token = None
                    headers["Authorization"] = "Bearer " + await self.access_token()
                    continue
                response.raise_for_status()
                async for line in response.aiter_lines():
                    yield line
                return

    async def close(self) -> None:
        await self.http.aclose()
