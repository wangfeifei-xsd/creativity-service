"""连接检查和模型调用共用地址解析、凭据读取及 HTTP 客户端。"""

from collections.abc import Awaitable, Callable
from urllib.parse import urlsplit

import httpx
from pydantic import SecretBytes

from creativity_service.core.context import AuthContext
from creativity_service.core.database import assert_external_io_allowed
from creativity_service.core.primitives import ServiceError
from creativity_service.core.security.credentials import CredentialService
from creativity_service.core.security.outbound import ValidatedTarget, resolve_host
from creativity_service.integrations.models.transport import ModelTransport, RawCapture
from creativity_service.modules.models.policy import validate_endpoint
from creativity_service.modules.models.schemas import FrozenModel

Resolver = Callable[[str, int], Awaitable[list[str]]]
TransportFactory = Callable[[], httpx.AsyncBaseTransport]


class ModelConnectionClient:
    def __init__(
        self,
        credentials: CredentialService,
        resolver: Resolver = resolve_host,
        transport_factory: TransportFactory | None = None,
    ) -> None:
        self.credentials, self.resolver = credentials, resolver
        self.transport_factory = transport_factory

    async def call(
        self,
        context: AuthContext,
        config: FrozenModel,
        operation: Callable[[SecretBytes, httpx.AsyncClient], Awaitable[None]],
        before_send: Callable[[], Awaitable[None]],
        capture: RawCapture | None = None,
    ) -> None:
        """连接只使用已授权配置；解析结果不按公网、内网或 IP 范围过滤。"""
        assert_external_io_allowed()
        endpoint = validate_endpoint(config.protocol, config.endpoint)
        parsed = urlsplit(endpoint)
        hostname = (parsed.hostname or "").encode("idna").decode().lower()
        port = parsed.port or 443
        addresses = await self.resolver(hostname, port)
        if not addresses:
            raise ServiceError("DESTINATION_UNAVAILABLE", "模型地址解析失败", 503)
        target = ValidatedTarget(endpoint, hostname, port, tuple(addresses), {})

        async def invoke(secret: SecretBytes) -> None:
            # DNS 和凭据读取均可能等待，实际发送前由调用方复核权限及配置修订。
            await before_send()
            async with httpx.AsyncClient(
                transport=ModelTransport(
                    target,
                    capture if capture is not None else RawCapture(),
                    self.transport_factory() if self.transport_factory else None,
                ),
                timeout=config.timeout_seconds,
                follow_redirects=False,
                trust_env=False,
            ) as client:
                await operation(secret, client)

        await self.credentials.call(context, config.provider_credential_id, "model", invoke)
