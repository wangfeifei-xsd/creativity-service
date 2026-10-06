"""供应商鉴权和连通性检查，只读取协议模型目录，不生成内容。"""

from collections.abc import Callable

import httpx
from pydantic import SecretBytes

from creativity_service.core.primitives import ServiceError
from creativity_service.core.security.outbound import ValidatedTarget
from creativity_service.integrations.models.transport import ModelTransport, RawCapture
from creativity_service.modules.models.schemas import FrozenModel


async def probe_connection(
    config: FrozenModel,
    target: ValidatedTarget,
    secret: SecretBytes,
    *,
    transport_factory: Callable[[], httpx.AsyncBaseTransport] | None = None,
) -> None:
    """沿用固定 IP、TLS 域名检查和禁止重定向的传输，最多等待十五秒。"""
    token = secret.get_secret_value().decode()
    if config.protocol == "chat_completions":
        url = config.endpoint.rstrip("/") + "/models"
        headers = {"authorization": "Bearer " + token}
    elif config.protocol == "anthropic_messages":
        url = config.endpoint.removesuffix("/v1").rstrip("/") + "/v1/models?limit=1"
        headers = {"x-api-key": token, "anthropic-version": "2023-06-01"}
    else:
        raise ServiceError("MODEL_PROTOCOL_DISABLED", "该协议尚未启用连接检查", 422)
    async with httpx.AsyncClient(
        transport=ModelTransport(
            target, RawCapture(), transport_factory() if transport_factory else None
        ),
        timeout=min(config.timeout_seconds, 15),
        follow_redirects=False,
        trust_env=False,
    ) as client:
        response = await client.get(url, headers=headers)
    if response.status_code in {401, 403}:
        raise ServiceError("MODEL_AUTH_FAILED", "供应商鉴权失败，请检查连接凭据及其访问权限", 422)
    if response.status_code == 429:
        raise ServiceError("MODEL_RATE_LIMITED", "供应商请求受限，请稍后重试", 422)
    if response.status_code in {404, 405}:
        raise ServiceError(
            "MODEL_PROBE_UNSUPPORTED", "基础地址不正确或供应商不支持模型目录检查", 422
        )
    if response.status_code != 200:
        raise ServiceError("MODEL_PROVIDER_UNAVAILABLE", "供应商服务暂不可用", 502)
    try:
        body = response.json()
    except ValueError as exc:
        raise ServiceError("MODEL_RESPONSE_INVALID", "供应商返回的模型目录格式不正确", 502) from exc
    if not isinstance(body, dict) or not isinstance(body.get("data"), list):
        raise ServiceError("MODEL_RESPONSE_INVALID", "供应商返回的模型目录格式不正确", 502)
