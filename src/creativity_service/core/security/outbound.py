"""HTTP 与 HTTPS 配置只校验格式；连接时解析并固定地址。"""

import socket
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, replace
from typing import Literal
from urllib.parse import unquote, urljoin, urlsplit

from creativity_service.core.context import Scope
from creativity_service.core.database import assert_external_io_allowed
from creativity_service.core.primitives import ServiceError

Purpose = Literal["model", "mcp", "http_tool", "oauth", "webhook", "identity"]


@dataclass(frozen=True)
class Destination:
    """兼容历史出站配置，许可字段不再参与地址或请求头校验。"""

    channel_id: str
    environment: str
    purpose: Purpose
    hostname: str
    port: int = 443
    scheme: str = "https"
    allowed_networks: tuple[str, ...] = ()
    path_prefix: str = "/"
    allowed_headers: frozenset[str] = frozenset({"accept", "content-type", "x-request-id"})


@dataclass(frozen=True)
class ValidatedTarget:
    url: str
    hostname: str
    port: int
    addresses: tuple[str, ...]
    headers: dict[str, str]


async def resolve_host(hostname: str, port: int) -> list[str]:
    import asyncio

    addresses = await asyncio.to_thread(socket.getaddrinfo, hostname, port, type=socket.SOCK_STREAM)
    return sorted({str(item[4][0]) for item in addresses})


class OutboundPolicy:
    def __init__(
        self,
        destinations: tuple[Destination, ...],
        resolver: Callable[[str, int], Awaitable[list[str]]] = resolve_host,
    ) -> None:
        self.destinations, self.resolver = destinations, resolver

    async def validate_configuration(self, scope: Scope, purpose: Purpose, url: str) -> None:
        """保存配置只检查格式，不依赖 DNS、连通性或目的地许可。"""
        self._prepare(url)

    async def validate(
        self, scope: Scope, purpose: Purpose, url: str, headers: Mapping[str, str] | None = None
    ) -> ValidatedTarget:
        assert_external_io_allowed()
        target = self._prepare(url, headers)
        return await self._resolve(target)

    def _prepare(self, url: str, headers: Mapping[str, str] | None = None) -> ValidatedTarget:
        denied = ServiceError("DESTINATION_FORBIDDEN", "出站目的地或请求头未获授权", 403)
        try:
            parsed = urlsplit(url)
            port = (
                parsed.port
                if parsed.port is not None
                else (443 if parsed.scheme == "https" else 80)
            )
            hostname = (parsed.hostname or "").encode("idna").decode().lower()
        except (ValueError, UnicodeError) as exc:
            raise denied from exc
        decoded_path = unquote(parsed.path or "/")
        if (
            parsed.scheme not in {"https", "http"}
            or not hostname
            or port < 1
            or parsed.username
            or parsed.password
            or parsed.fragment
            or "\\" in url
            or any(ord(c) < 33 for c in url)
            or any(segment in {".", ".."} for segment in decoded_path.split("/"))
        ):
            raise denied
        normalized: dict[str, str] = {}
        blocked = {
            "host",
            "connection",
            "content-length",
            "transfer-encoding",
            "cookie",
            "proxy-authorization",
            "authorization",
            "forwarded",
            "x-forwarded-for",
        }
        for name, value in (headers or {}).items():
            key = name.lower()
            if (
                key in normalized
                or key in blocked
                or key not in {"accept", "content-type", "x-request-id"}
                or not key.replace("-", "").isalnum()
                or any(ord(char) < 32 or ord(char) == 127 for char in value)
            ):
                raise denied
            normalized[key] = value
        return ValidatedTarget(url, hostname, port, (), normalized)

    async def _resolve(self, target: ValidatedTarget) -> ValidatedTarget:
        assert_external_io_allowed()
        try:
            addresses = await self.resolver(target.hostname, target.port)
        except OSError:
            raise ServiceError("DESTINATION_UNAVAILABLE", "目的地解析失败", 503) from None
        if not addresses:
            raise ServiceError("DESTINATION_UNAVAILABLE", "目的地解析失败", 503)
        # 解析结果不按网络范围过滤；传输层固定解析地址，HTTPS 保留域名 TLS 校验。
        return replace(target, addresses=tuple(addresses))

    async def redirect(
        self, scope: Scope, purpose: Purpose, previous: ValidatedTarget, location: str, hop: int
    ) -> ValidatedTarget:
        if hop < 1 or hop > 3:
            raise ServiceError("REDIRECT_LIMIT", "重定向次数超限", 403)
        # 不把来源认证头转发给重定向目标；适配器只能在新授权边界重新注入凭据。
        return await self.validate(scope, purpose, urljoin(previous.url, location))
