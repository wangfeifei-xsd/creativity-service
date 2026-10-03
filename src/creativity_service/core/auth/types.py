"""认证内部契约，只能由服务端可信读取器构造。"""

from datetime import datetime
from typing import Literal, Protocol, Self

from pydantic import AwareDatetime, Field, PrivateAttr, model_validator

from creativity_service.core.context import AuthContext, Environment, Scope
from creativity_service.core.primitives import Contract, Digest, Identifier, Revision

Purpose = Literal["login", "management", "service"]
Status = Literal["ACTIVE", "DISABLED"]


class AccountState(Contract):
    id: Identifier
    login_name: str
    display_name: str
    platform_roles: list[str]
    status: Status
    must_change_password: bool
    credential_version: Revision
    revision: Revision


class MembershipState(Contract):
    custom_actions: frozenset[str] = Field(default_factory=frozenset)
    id: Identifier
    channel_id: Identifier
    user_id: Identifier
    roles: list[str]
    environments: list[Environment]
    data_scopes: list[Identifier]
    status: Status
    revision: Revision


class GrantState(Contract):
    id: Identifier
    channel_id: Identifier
    grantee_type: Literal["account", "role"]
    grantee_id: Identifier
    resource_type: Identifier
    resource_id: str
    allowed_actions: list[str]
    environments: list[Environment]
    data_scopes: list[Identifier]
    revision: Revision


class TokenRecord(Contract):
    _stored_json: str | None = PrivateAttr(default=None)
    upstream_expires_at: AwareDatetime | None = None
    identity_channel_id: Identifier | None = None
    channel_id: Identifier
    token_digest: Digest
    session_id: Identifier
    purpose: Purpose
    principal_id: Identifier
    principal_type: Literal["management", "service"]
    environment: Environment | None = None
    data_scope_id: Identifier | None = None
    client_id: Identifier | None = None
    key_id: Identifier | None = None
    credential_version: Revision | None = None
    membership_version: Revision | None = None
    issued_at_ms: int
    issued_at: AwareDatetime
    expires_at: AwareDatetime
    index_keys: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def valid_binding(self) -> Self:
        if self.expires_at <= self.issued_at:
            raise ValueError("令牌有效期不正确")
        if self.purpose == "login":
            if self.channel_id != "system" or any(
                (
                    self.environment,
                    self.data_scope_id,
                    self.client_id,
                    self.key_id,
                    self.membership_version,
                )
            ):
                raise ValueError("临时登录身份只能归系统渠道")
        elif self.channel_id == "system" or self.environment is None:
            raise ValueError("工作身份必须绑定业务渠道与环境")
        if self.purpose == "service":
            if (
                self.principal_type != "service"
                or not self.client_id
                or not self.key_id
                or self.principal_id != self.client_id
                or self.credential_version is not None
            ):
                raise ValueError("服务身份绑定不完整")
        elif self.principal_type != "management" or not self.credential_version:
            raise ValueError("账号身份绑定不完整")
        if self.purpose == "management" and (not self.membership_version or not self.data_scope_id):
            raise ValueError("工作区身份必须绑定成员与数据域")
        return self


class TokenResponse(Contract):
    access_token: str = Field(repr=False)
    token_type: Literal["Bearer"] = "Bearer"
    expires_in: int
    expires_at: AwareDatetime
    must_change_password: bool = False


class CurrentIdentityReader(Protocol):
    async def account(self, user_id: str) -> AccountState | None: ...
    async def membership(self, channel_id: str, user_id: str) -> MembershipState | None: ...
    async def grants(self, channel_id: str) -> list[GrantState]: ...
    async def token_revoked(self, channel_id: str, token_digest: str) -> bool: ...


class ServiceIdentity(Contract):
    channel_id: Identifier
    environment: Environment
    client_id: Identifier
    key_id: Identifier
    expires_at: AwareDatetime
    client_actions: frozenset[str]
    key_actions: frozenset[str]
    data_scopes: frozenset[str]


class ServiceIdentityReader(Protocol):
    async def read_current(self, context: AuthContext) -> ServiceIdentity: ...


class SubjectAuthority(Contract):
    scope: Scope
    expires_at: AwareDatetime
    actions: frozenset[str]
    agent_actions: frozenset[str]
    resources: dict[str, frozenset[str]]


class SubjectAuthorityReader(Protocol):
    async def read_current(self, context: AuthContext) -> SubjectAuthority: ...


class ResourceState(Contract):
    scope: Scope
    resource_type: str
    resource_id: str
    name: str
    active: bool


class ResourceStateReader(Protocol):
    async def read_current(
        self, context: AuthContext, resource_type: str, resource_id: str
    ) -> ResourceState | None: ...


class WorkspaceOption(Contract):
    channel_id: Identifier
    channel_name: str
    environment: Environment
    environment_name: str
    data_scope_id: Identifier
    data_scope_name: str


class WorkspaceDirectory(Protocol):
    async def list_for(self, user_id: str) -> list[WorkspaceOption]: ...


class AuthorizationDecision(Contract):
    allowed: bool
    actions: list[str]
    reason: str | None = None


class IdentitySource(Contract):
    """运行保存原始身份来源，不保存浏览器 Token 或历史权限快照。"""

    scope: Scope
    source_type: Literal["management", "service"]
    principal_id: Identifier
    actor_id: Identifier | None = None
    client_id: Identifier | None = None
    key_id: Identifier | None = None
    delegation_id: Identifier | None = None

    @model_validator(mode="after")
    def validate_source(self) -> Self:
        if self.source_type == "management":
            if self.actor_id != self.principal_id or self.client_id or self.key_id:
                raise ValueError("管理运行身份来源不完整")
        elif self.client_id != self.principal_id or not self.key_id or self.actor_id:
            raise ValueError("服务运行身份来源不完整")
        return self

    def worker_context(self, request_id: str) -> AuthContext:
        return AuthContext(
            scope=self.scope,
            principal_type="worker",
            principal_id=self.principal_id,
            actor_id=self.actor_id,
            client_id=self.client_id,
            key_id=self.key_id,
            delegation_id=self.delegation_id,
            request_id=request_id,
        )


class Revocation(Contract):
    id: Identifier
    channel_id: Identifier
    kind: Literal["account", "member", "key", "token"]
    target_id: str
    cutoff_at: datetime
