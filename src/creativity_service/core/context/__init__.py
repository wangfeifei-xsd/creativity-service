"""仅由服务端验证器建立上下文，HTTP 正文与队列消息不构成授权。"""

from collections.abc import AsyncIterator, Iterator
from contextlib import asynccontextmanager, contextmanager
from contextvars import ContextVar
from typing import Annotated, Literal, Protocol, Self

from fastapi import Depends, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import Field, model_validator

from creativity_service.core.primitives import (
    Contract,
    Digest,
    Identifier,
    ServiceError,
    new_id,
    unavailable,
)

SYSTEM_CHANNEL_ID = "system"
Environment = Literal["dev", "test", "fat", "prod"]


class Scope(Contract):
    channel_id: Identifier
    environment: Environment
    data_scope_id: Identifier | None = None
    subject_type: Identifier | None = None
    subject_id: Identifier | None = None

    @model_validator(mode="after")
    def validate_scope(self) -> Self:
        if self.channel_id == SYSTEM_CHANNEL_ID:
            raise ValueError("系统渠道不能作为业务范围")
        if (self.subject_type is None) != (self.subject_id is None):
            raise ValueError("主体类型与编号必须同时存在")
        if self.subject_id is not None and self.data_scope_id is None:
            raise ValueError("主体必须属于明确数据域")
        return self


class ControlScope(Contract):
    channel_id: Literal["system"] = "system"
    purpose: Literal[
        "identity_lookup",
        "channel_directory",
        "platform_limits",
        "accounts",
        "roles",
        "catalog",
        "templates",
        "audit",
    ]
    actor_id: Identifier


class ControlAuthContext(Contract):
    scope: ControlScope
    principal_type: Literal["management"] = "management"
    principal_id: Identifier
    request_id: Identifier
    session_id: Identifier
    token_digest: Digest
    granted_actions: frozenset[str] = Field(default_factory=frozenset)


class AuthContext(Contract):
    scope: Scope
    principal_type: Literal["management", "service", "worker"]
    principal_id: Identifier
    request_id: Identifier
    client_id: Identifier | None = None
    key_id: Identifier | None = None
    actor_id: Identifier | None = None
    session_id: Identifier | None = None
    token_digest: Digest | None = None
    delegation_id: Identifier | None = None
    granted_actions: frozenset[str] = Field(default_factory=frozenset)

    @model_validator(mode="after")
    def validate_identity(self) -> Self:
        if self.principal_type == "service" and (not self.client_id or not self.key_id):
            raise ValueError("服务身份必须包含调用服务与密钥标识")
        if self.principal_type == "management" and not self.actor_id:
            raise ValueError("管理身份必须包含操作人")
        return self


class ChannelState(Contract):
    channel_id: Identifier
    environment: Environment
    channel_active: bool
    environment_active: bool
    membership_active: bool | None
    client_active: bool | None
    key_active: bool | None
    data_scope_active: bool | None
    channel_status: Literal["ACTIVE", "SUSPENDED", "ARCHIVED"] | None = None


class ChannelStateReader(Protocol):
    async def read_current(self, context: AuthContext) -> ChannelState: ...


class AuthenticationResolver(Protocol):
    async def authenticate(self, bearer: str, purpose: str) -> AuthContext: ...


class Authorization(Protocol):
    async def require(self, context: AuthContext, action: str, resource_id: str) -> None: ...


class DenyAuthorization:
    async def require(self, context: AuthContext, action: str, resource_id: str) -> None:
        raise unavailable("实时授权服务")


current_context: ContextVar[AuthContext | None] = ContextVar("auth_context", default=None)


@contextmanager
def bind_context(context: AuthContext) -> Iterator[AuthContext]:
    if not isinstance(context, AuthContext):
        raise ServiceError("CONTEXT_REQUIRED", "缺少受信上下文", 403)
    token = current_context.set(context)
    try:
        yield context
    finally:
        current_context.reset(token)


bearer_scheme = HTTPBearer(auto_error=False, scheme_name="PlatformToken", bearerFormat="不透明令牌")


async def require_http_context(
    request: Request,
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer_scheme)],
) -> AsyncIterator[AuthContext]:
    resolver: AuthenticationResolver | None = getattr(request.app.state, "authentication", None)
    if resolver is None:
        raise unavailable("身份认证服务")
    if credentials is None:
        raise ServiceError("UNAUTHENTICATED", "请重新登录", 401)
    purpose = "management" if request.url.path.startswith("/admin/") else "service"
    context = await resolver.authenticate(credentials.credentials, purpose)
    if not context.session_id or not context.token_digest:
        raise unavailable("会话复核凭据")
    context = context.model_copy(
        update={"request_id": getattr(request.state, "request_id", None) or new_id("request")}
    )
    if context.principal_type != purpose:
        raise ServiceError("TOKEN_PURPOSE_INVALID", "凭据用途不符", 401)
    if purpose == "service":
        delegation = getattr(request.app.state, "delegation", None)
        if delegation is None:
            raise unavailable("业务主体委托验证服务")
        context = await delegation.verify_request(context, request)
    with bind_context(context):
        yield context


class TaskEnvelope(Contract):
    channel_id: Identifier
    run_id: Identifier


class TaskContextReader(Protocol):
    async def load_and_authorize(self, message: TaskEnvelope) -> AuthContext: ...


@asynccontextmanager
async def task_context(
    message: TaskEnvelope, reader: TaskContextReader | None
) -> AsyncIterator[AuthContext]:
    # 消息只能定位；身份、范围与当前状态必须来自持久化运行及服务端授权。
    current_context.set(None)
    try:
        if reader is None:
            raise unavailable("任务身份核对服务")
        context = await reader.load_and_authorize(message)
        if context.scope.channel_id != message.channel_id:
            raise ServiceError("TASK_SCOPE_MISMATCH", "任务归属不符", 403)
        context = context.model_copy(
            update={"principal_type": "worker", "session_id": None, "token_digest": None}
        )
        with bind_context(context):
            yield context
    finally:
        current_context.set(None)


async def require_channel_state(
    context: AuthContext, reader: ChannelStateReader | None, *, governance: bool = False
) -> ChannelState:
    if reader is None:
        raise unavailable("渠道当前状态服务")
    state = await reader.read_current(context)
    if not isinstance(state, ChannelState):
        raise unavailable("渠道当前状态对象")
    if (
        state.channel_id != context.scope.channel_id
        or state.environment != context.scope.environment
    ):
        raise ServiceError("CHANNEL_UNAVAILABLE", "渠道或环境不可用", 403)
    governed = governance and context.principal_type == "management" and bool(context.actor_id)
    if not governed and (not state.channel_active or not state.environment_active):
        if state.channel_status == "SUSPENDED":
            raise ServiceError("CHANNEL_SUSPENDED", "渠道已暂停", 403)
        if state.channel_status == "ARCHIVED":
            raise ServiceError("CHANNEL_ARCHIVED", "渠道已归档", 403)
        raise ServiceError("CHANNEL_UNAVAILABLE", "渠道或环境不可用", 403)
    if context.actor_id and state.membership_active is not True:
        raise ServiceError("MEMBERSHIP_DISABLED", "渠道成员不可用", 403)
    if context.client_id and (state.client_active is not True or state.key_active is not True):
        raise ServiceError("CLIENT_REVOKED", "接入身份不可用", 403)
    if not governed and context.scope.data_scope_id and state.data_scope_active is not True:
        raise ServiceError("DATA_SCOPE_DISABLED", "业务数据域不可用", 403)
    return state
