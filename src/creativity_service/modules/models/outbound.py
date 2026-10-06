"""用已鉴权的供应商连接授权模型出站，不重复登记服务器目的地。"""

import ipaddress
from urllib.parse import urlsplit

from creativity_service.core.context import Scope
from creativity_service.core.primitives import ServiceError
from creativity_service.core.security.outbound import Destination, OutboundPolicy, ValidatedTarget

# 内网模型可显式授权；本机服务、链路地址及云元数据始终不可作为模型目的地。
BLOCKED_NETWORKS = tuple(
    ipaddress.ip_network(value)
    for value in (
        "0.0.0.0/8",
        "127.0.0.0/8",
        "169.254.0.0/16",
        "224.0.0.0/4",
        "240.0.0.0/4",
        "100.100.100.200/32",
        "::/128",
        "::1/128",
        "::ffff:0:0/96",
        "fe80::/10",
        "ff00::/8",
    )
)


def normalize_networks(values: list[str]) -> list[str]:
    """规范化管理员提交的 CIDR；不允许通过大网段覆盖永久禁止的地址。"""
    result = []
    for value in values:
        try:
            if "/" not in value or "%" in value:
                raise ValueError
            network = ipaddress.ip_network(value.strip(), strict=True)
        except ValueError as exc:
            raise ServiceError(
                "MODEL_NETWORK_INVALID", "请填写有效的 IP 网段（CIDR）", 422
            ) from exc
        if any(network.overlaps(blocked) for blocked in BLOCKED_NETWORKS):
            raise ServiceError(
                "MODEL_NETWORK_INVALID", "IP 范围不能包含本机、链路或元数据等保留地址", 422
            )
        result.append(str(network))
    return sorted(set(result))


async def validate_connection_target(
    scope: Scope,
    endpoint: str,
    allowed_networks: list[str],
    override: OutboundPolicy | None = None,
) -> ValidatedTarget:
    """只接收服务层已授权连接；复用公共 DNS 校验与固定地址传输。"""
    networks = normalize_networks(allowed_networks)
    try:
        parsed = urlsplit(endpoint)
        hostname = (parsed.hostname or "").encode("idna").decode().lower()
        port = parsed.port or 443
        if parsed.scheme != "https" or not hostname:
            raise ValueError
    except (ValueError, UnicodeError) as exc:
        raise ServiceError(
            "MODEL_ENDPOINT_INVALID", "模型基础地址必须是有效的 HTTPS 地址", 422
        ) from exc
    policy = override or OutboundPolicy(
        (
            Destination(
                channel_id=scope.channel_id,
                environment=scope.environment,
                purpose="model",
                hostname=hostname,
                port=port,
                allowed_networks=tuple(networks),
            ),
        )
    )
    target = await policy.validate(scope, "model", endpoint)
    for address in target.addresses:
        ip = ipaddress.ip_address(address)
        if any(ip in blocked for blocked in BLOCKED_NETWORKS) or (
            networks and not any(ip in ipaddress.ip_network(value) for value in networks)
        ):
            raise ServiceError("DESTINATION_FORBIDDEN", "模型地址不在允许的 IP 范围内", 403)
    return target
