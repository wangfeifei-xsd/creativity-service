"""渠道凭据认证后接收业务后端的用户标识，不再配置委托签名或远端复核。"""

from fastapi import Request
from pydantic import ValidationError
from sqlalchemy.ext.asyncio import AsyncEngine

from creativity_service.core.context import AuthContext, Scope
from creativity_service.core.primitives import ServiceError
from creativity_service.modules.integrations.subject_content import initialize_subject_content


class ChannelBusinessContext:
    def __init__(self, engine: AsyncEngine) -> None:
        self.engine = engine

    async def resolve(self, context: AuthContext, request: Request) -> AuthContext:
        """用户标识只决定内容归属，渠道和环境始终取已验证的服务 Token。"""
        kinds = request.headers.getlist("X-Subject-Type")
        identifiers = request.headers.getlist("X-Subject-ID")
        if len(kinds) > 1 or len(identifiers) > 1 or bool(kinds) != bool(identifiers):
            raise ServiceError("SUBJECT_INVALID", "用户类型和用户编号须成对提供", 422)
        try:
            scope = Scope(
                channel_id=context.scope.channel_id,
                environment=context.scope.environment,
                subject_type=kinds[0] if kinds else "service",
                subject_id=identifiers[0] if identifiers else context.client_id,
            )
        except ValidationError:
            raise ServiceError("SUBJECT_INVALID", "用户类型或用户编号格式无效", 422) from None
        resolved = context.model_copy(update={"scope": scope})
        await initialize_subject_content(self.engine, resolved)
        return resolved
