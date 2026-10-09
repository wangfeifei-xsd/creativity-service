"""供应商鉴权和连通性检查，只读取协议模型目录，不生成内容。"""

import httpx
from pydantic import SecretBytes

from creativity_service.core.primitives import ServiceError
from creativity_service.modules.models.schemas import FrozenModel


async def probe_connection(
    config: FrozenModel,
    secret: SecretBytes,
    client: httpx.AsyncClient,
) -> None:
    """使用统一连接入口提供的客户端读取模型目录，不另建传输。"""
    token = secret.get_secret_value().decode()
    if config.protocol == "chat_completions":
        url = config.endpoint.rstrip("/") + "/models"
        headers = {"authorization": "Bearer " + token}
    elif config.protocol == "anthropic_messages":
        url = config.endpoint.removesuffix("/v1").rstrip("/") + "/v1/models?limit=1"
        headers = {"x-api-key": token, "anthropic-version": "2023-06-01"}
    else:
        raise ServiceError("MODEL_PROTOCOL_DISABLED", "该协议尚未启用连接检查", 422)
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
