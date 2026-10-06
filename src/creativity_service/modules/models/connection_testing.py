"""模型连接检查只验证配置，不授予业务执行权限或标记模型能力。"""

import asyncio
from time import monotonic
from typing import Any

import httpx
from pydantic import SecretBytes

from creativity_service.core.auth.authentication import AdminSession
from creativity_service.core.context import AuthContext
from creativity_service.core.database import UnitOfWork, transaction
from creativity_service.core.deletion import ContentRef, DeletionGuard, content_key
from creativity_service.core.primitives import ServiceError, utcnow
from creativity_service.integrations.models.probe import probe_connection
from creativity_service.modules.models.outbound import validate_connection_target
from creativity_service.modules.models.policy import PROTOCOLS
from creativity_service.modules.models.repositories import model_key, repository, required
from creativity_service.modules.models.schemas import ConnectionTestView
from creativity_service.modules.models.services import ModelService

ERRORS = {
    "DESTINATION_FORBIDDEN": "模型地址不在允许的 IP 范围内，请检查连接配置",
    "DESTINATION_UNAVAILABLE": "模型地址解析失败，请检查基础地址和网络",
    "MODEL_REDIRECT_FORBIDDEN": "供应商返回了重定向，请填写最终基础地址",
    "CREDENTIAL_UNAVAILABLE": "连接凭据已停用或不可用，请更新凭据",
    "CREDENTIAL_DECRYPT_FAILED": "连接凭据无法解密，请检查主密钥或重新保存凭据",
    "CREDENTIAL_KEY_UNAVAILABLE": "连接凭据的加密主密钥不可用",
    "MODEL_AUTH_FAILED": "供应商鉴权失败，请检查连接凭据及其访问权限",
    "MODEL_RATE_LIMITED": "供应商请求受限，请稍后重试",
    "MODEL_PROBE_UNSUPPORTED": "基础地址不正确或供应商不支持模型目录检查",
    "MODEL_RESPONSE_INVALID": "供应商返回的模型目录格式不正确",
    "MODEL_PROVIDER_UNAVAILABLE": "供应商服务暂不可用",
}


class ModelConnectionTesting:
    def __init__(self, service: ModelService) -> None:
        self.service = service

    async def load(
        self, uow: UnitOfWork, context: AuthContext, model_id: str
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        model = await required(uow.connection, context.scope, "models", model_id)
        connection = await required(
            uow.connection, context.scope, "model_connections", model["connection_id"]
        )
        await DeletionGuard(context.scope).check(
            uow,
            [
                ContentRef("model", model_id),
                ContentRef("model_connection", connection["id"]),
                ContentRef("version", model["current_version_id"]),
                ContentRef("version", connection["current_version_id"]),
            ],
        )
        if model["status"] != "ACTIVE" or connection["status"] != "ACTIVE":
            raise ServiceError("MODEL_DISABLED", "模型或连接已停用", 409)
        if not PROTOCOLS[connection["protocol"]].enabled:
            raise ServiceError("MODEL_PROTOCOL_DISABLED", "该协议尚未启用连接检查", 422)
        return model, connection

    @staticmethod
    def unchanged(
        initial: tuple[dict[str, Any], dict[str, Any]],
        current: tuple[dict[str, Any], dict[str, Any]],
    ) -> None:
        if any(
            (old["id"], old["revision"]) != (new["id"], new["revision"])
            for old, new in zip(initial, current, strict=True)
        ):
            raise ServiceError("REVISION_CONFLICT", "测试期间配置已变化，请重新测试", 409)

    async def check(self, session: AdminSession, model_id: str) -> ConnectionTestView:
        service = self.service
        context = await service.context(session)
        keys = [model_key(context.scope.channel_id), content_key(context.scope)]
        async with transaction(service.engine, context.scope, keys) as uow:
            initial = await self.load(uow, context, model_id)
        model, connection = initial
        config = service.snapshot(context, model, connection)
        started = monotonic()
        error_code, message = None, "连接成功，供应商接口与凭据检查通过"
        try:
            # 包含 DNS、密钥读取及 HTTP 的总时限；所有外部等待都在事务外。
            async with asyncio.timeout(min(config.timeout_seconds, 15)):
                target = await validate_connection_target(
                    context.scope, config.endpoint, config.allowed_networks, service.outbound
                )

                async def invoke(secret: SecretBytes) -> None:
                    await service.iam.authorization.boundary(
                        context, "model:manage", "channel", context.scope.channel_id
                    )
                    async with transaction(service.engine, context.scope, keys) as uow:
                        self.unchanged(initial, await self.load(uow, context, model_id))
                    await probe_connection(config, target, secret)

                await service.credentials.call(
                    context, config.provider_credential_id, "model", invoke
                )
        except ServiceError as exc:
            if exc.code in {
                "FORBIDDEN",
                "UNAUTHENTICATED",
                "REVISION_CONFLICT",
                "MODEL_DISABLED",
                "CONTENT_DELETED",
                "NOT_FOUND",
            }:
                raise
            error_code = exc.code
            message = ERRORS.get(exc.code, "连接检查失败，请检查连接配置")
        except (TimeoutError, httpx.TimeoutException):
            error_code, message = "MODEL_TIMEOUT", "连接检查超时，请检查地址或稍后重试"
        except (OSError, httpx.TransportError):
            error_code, message = "MODEL_NETWORK_ERROR", "无法连接供应商，请检查地址、网络和证书"
        checked_at = utcnow()
        latency = int((monotonic() - started) * 1000)
        # 外部请求返回后复核权限和修订，禁止旧结果覆盖已修改配置。
        async with service.mutation(session, "model_connections", connection["id"]) as (
            uow,
            current_context,
        ):
            self.unchanged(initial, await self.load(uow, current_context, model_id))
            await repository(context.scope, "model_connections").change(
                uow,
                connection["id"],
                connection["revision"],
                {
                    "health_status": "HEALTHY" if error_code is None else "UNAVAILABLE",
                    "health_reason": None if error_code is None else message,
                    "health_checked_at": checked_at,
                },
            )
        return ConnectionTestView(
            model_id=model_id,
            connection_id=connection["id"],
            success=error_code is None,
            message=message,
            error_code=error_code,
            latency_ms=latency,
            checked_at=checked_at,
        )
