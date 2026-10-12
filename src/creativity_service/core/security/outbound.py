"""HTTPS 配置直接放行，HTTP 按用途校验许可；连接时固定解析地址。"""

import ipaddress
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
        """保存 HTTPS 配置只检查格式，不依赖 DNS、连通性或目的地许可。"""
        target, destination = self._prepare(scope, purpose, url)
        if destination.scheme != "https":
            await self._resolve(target, destination)

    async def validate(
        self, scope: Scope, purpose: Purpose, url: str, headers: Mapping[str, str] | None = None
    ) -> ValidatedTarget:
        assert_external_io_allowed()
        target, destination = self._prepare(scope, purpose, url, headers)
        return await self._resolve(target, destination)

    def _prepare(
        self, scope: Scope, purpose: Purpose, url: str, headers: Mapping[str, str] | None = None
    ) -> tuple[ValidatedTarget, Destination]:
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
        if parsed.scheme == "https":
            # 所有 HTTPS 配置均直接放行，历史目的地规则不再限制域名、路径或 IP。
            destination = Destination(scope.channel_id, scope.environment, purpose, hostname, port)
        else:
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
        return ValidatedTarget(url, hostname, port, (), normalized), destination

    async def _resolve(self, target: ValidatedTarget, destination: Destination) -> ValidatedTarget:
        assert_external_io_allowed()
        try:
            addresses = await self.resolver(target.hostname, target.port)
        except OSError:
            raise ServiceError("DESTINATION_UNAVAILABLE", "目的地解析失败", 503) from None
        if not addresses:
            raise ServiceError("DESTINATION_UNAVAILABLE", "目的地解析失败", 503)
        if destination.scheme != "https":
            denied = ServiceError("DESTINATION_FORBIDDEN", "出站目的地或请求头未获授权", 403)
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
        # HTTPS 解析结果不按网络范围过滤；连接仍保留 hostname 的 TLS 校验。
        return replace(target, addresses=tuple(addresses))

    async def redirect(
        self, scope: Scope, purpose: Purpose, previous: ValidatedTarget, location: str, hop: int
    ) -> ValidatedTarget:
        if hop < 1 or hop > 3:
            raise ServiceError("REDIRECT_LIMIT", "重定向次数超限", 403)
        # 不把来源认证头转发给重定向目标；适配器只能在新授权边界重新注入凭据。
        return await self.validate(scope, purpose, urljoin(previous.url, location))
