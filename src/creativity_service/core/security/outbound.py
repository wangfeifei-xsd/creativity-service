"""分别登记模型、MCP 与 HTTP 工具目的地，连接时固定已验证地址。"""

import ipaddress
import socket
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from typing import Literal
from urllib.parse import unquote, urljoin, urlsplit

from creativity_service.core.context import Scope
from creativity_service.core.database import assert_external_io_allowed
from creativity_service.core.primitives import ServiceError

Purpose = Literal["model", "mcp", "http_tool", "oauth", "webhook", "identity"]


@dataclass(frozen=True)
class Destination:
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

    async def validate(
        self, scope: Scope, purpose: Purpose, url: str, headers: Mapping[str, str] | None = None
    ) -> ValidatedTarget:
        assert_external_io_allowed()
        denied = ServiceError("DESTINATION_FORBIDDEN", "出站目的地或请求头未获授权", 403)
        try:
            parsed = urlsplit(url)
            port = parsed.port or (443 if parsed.scheme == "https" else 80)
            hostname = (parsed.hostname or "").encode("idna").decode().lower()
        except (ValueError, UnicodeError) as exc:
            raise denied from exc
        decoded_path = unquote(parsed.path or "/")
        if (
            parsed.scheme not in {"https", "http"}
            or parsed.username
            or parsed.password
            or parsed.fragment
            or "\\" in url
            or any(ord(c) < 33 for c in url)
            or any(segment in {".", ".."} for segment in decoded_path.split("/"))
        ):
            raise denied
        candidates = [
            d
            for d in self.destinations
            if (d.channel_id, d.environment, d.purpose, d.hostname.lower(), d.port, d.scheme)
            == (scope.channel_id, scope.environment, purpose, hostname, port, parsed.scheme)
            and (
                decoded_path == d.path_prefix.rstrip("/")
                or decoded_path.startswith(d.path_prefix.rstrip("/") + "/")
            )
        ]
        if len(candidates) != 1:
            raise denied
        destination = candidates[0]
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
                or key not in destination.allowed_headers
                or not key.replace("-", "").isalnum()
                or any(ord(char) < 32 or ord(char) == 127 for char in value)
            ):
                raise denied
            normalized[key] = value
        addresses = await self.resolver(hostname, port)
        if not addresses:
            raise ServiceError("DESTINATION_UNAVAILABLE", "目的地解析失败", 503)
        networks = [ipaddress.ip_network(network) for network in destination.allowed_networks]
        for address in addresses:
            ip = ipaddress.ip_address(address)
            if ip.is_unspecified or ip.is_multicast or ip.is_link_local:
                raise denied
            if networks:
                if not any(ip in network for network in networks):
                    raise denied
            elif not ip.is_global:
                raise denied
        # 适配器须用这些地址连接并保留 hostname 的 TLS 校验；不能再次 DNS 解析。
        return ValidatedTarget(url, hostname, port, tuple(addresses), normalized)

    async def redirect(
        self, scope: Scope, purpose: Purpose, previous: ValidatedTarget, location: str, hop: int
    ) -> ValidatedTarget:
        if hop < 1 or hop > 3:
            raise ServiceError("REDIRECT_LIMIT", "重定向次数超限", 403)
        # 不把来源认证头转发给重定向目标；适配器只能在新授权边界重新注入凭据。
        return await self.validate(scope, purpose, urljoin(previous.url, location))
