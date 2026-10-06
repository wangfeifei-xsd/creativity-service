"""每次请求及外部调用边界复核当前身份，后台身份独立于浏览器会话。"""

from dataclasses import dataclass

from creativity_service.core.auth.tokens import TokenStore
from creativity_service.core.auth.types import (
    AccountState,
    CurrentIdentityReader,
    IdentitySource,
    MembershipState,
    ServiceIdentity,
    ServiceIdentityReader,
    TokenRecord,
)
from creativity_service.core.context import (
    AuthContext,
    ChannelStateReader,
    ControlAuthContext,
    ControlScope,
    Scope,
    require_channel_state,
    verify_channel_state,
)
from creativity_service.core.primitives import ServiceError, new_id, unavailable, utcnow


@dataclass(frozen=True)
class AdminSession:
    token: TokenRecord
    account: AccountState
    context: AuthContext | ControlAuthContext


@dataclass(frozen=True)
class ValidatedIdentity:
    """一次身份复核的读取结果，仅供当前调用继续计算授权。"""

    account: AccountState | None = None
    member: MembershipState | None = None
    service: ServiceIdentity | None = None


class AuthenticationService:
    def __init__(
        self,
        tokens: TokenStore,
        identities: CurrentIdentityReader,
        channels: ChannelStateReader | None = None,
        services: ServiceIdentityReader | None = None,
    ) -> None:
        self.tokens, self.identities = tokens, identities
        self.channels, self.services = channels, services

    async def validate_channel(
        self, context: AuthContext, identity: ValidatedIdentity, *, governance: bool = False
    ) -> None:
        reader = getattr(self.channels, "read_validated", None)
        if reader is None:
            await require_channel_state(context, self.channels, governance=governance)
        else:
            state = await reader(context, member=identity.member, service=identity.service)
            verify_channel_state(context, state, governance=governance)

    async def revalidate_admin(
        self, session: AdminSession, *, allow_initial: bool = False, governance: bool = False
    ) -> None:
        record = await self.tokens.read_digest(session.token.token_digest, {"login", "management"})
        if not record.same_session(session.token):
            raise ServiceError("UNAUTHENTICATED", "会话归属不符", 401)
        await self.validate_record(record, allow_initial=allow_initial, governance=governance)

    async def identity_source(self, context: AuthContext) -> IdentitySource:
        await self.revalidate(context)
        return IdentitySource(
            scope=context.scope,
            source_type="management" if context.actor_id else "service",
            principal_id=context.principal_id,
            actor_id=context.actor_id,
            client_id=context.client_id,
            key_id=context.key_id,
            delegation_id=context.delegation_id,
        )

    async def active_account(self, user_id: str, *, allow_initial: bool = False) -> AccountState:
        account = await self.identities.account(user_id)
        if account is None:
            raise ServiceError("UNAUTHENTICATED", "请重新登录", 401)
        if account.status != "ACTIVE":
            raise ServiceError("ACCOUNT_DISABLED", "账号已停用", 401)
        if account.must_change_password and not allow_initial:
            raise ServiceError("PASSWORD_CHANGE_REQUIRED", "请先修改初始密码", 403)
        return account

    async def active_member(
        self, context: AuthContext, *, account: AccountState | None = None
    ) -> MembershipState:
        user_id = context.actor_id
        if not user_id or context.principal_id != user_id:
            raise ServiceError("UNAUTHENTICATED", "管理身份不完整", 401)
        if account is None:
            account = await self.active_account(user_id)
        if account.id != user_id or account.status != "ACTIVE" or account.must_change_password:
            raise ServiceError("UNAUTHENTICATED", "管理身份不可用", 401)
        member = await self.identities.membership(context.scope.channel_id, user_id)
        if member is None or member.status != "ACTIVE":
            raise ServiceError("MEMBERSHIP_DISABLED", "渠道成员已停用", 401)
        if (
            context.scope.environment not in member.environments
            or context.scope.data_scope_id not in member.data_scopes
        ):
            raise ServiceError("NOT_FOUND", "请求资源不存在", 404)
        return member

    async def service_identity(self, context: AuthContext) -> ServiceIdentity:
        if self.services is None:
            raise unavailable("接入身份当前状态服务")
        identity = await self.services.read_current(context)
        if (
            identity.channel_id != context.scope.channel_id
            or identity.environment != context.scope.environment
            or identity.client_id != context.client_id
            or identity.key_id != context.key_id
            or context.principal_id != identity.client_id
            or identity.expires_at <= utcnow()
        ):
            raise ServiceError("CLIENT_REVOKED", "接入身份不可用", 401)
        if context.scope.data_scope_id and context.scope.data_scope_id not in identity.data_scopes:
            raise ServiceError("NOT_FOUND", "请求资源不存在", 404)
        return identity

    async def validate_record(
        self,
        record: TokenRecord,
        *,
        allow_initial: bool = False,
        governance: bool = False,
        context: AuthContext | None = None,
    ) -> ValidatedIdentity:
        account, member, service = None, None, None
        if await self.identities.token_revoked(record.channel_id, record.token_digest):
            raise ServiceError("UNAUTHENTICATED", "请重新登录", 401)
        if record.purpose != "service":
            account = await self.active_account(record.principal_id, allow_initial=True)
            if account.credential_version != record.credential_version:
                raise ServiceError("UNAUTHENTICATED", "凭据已更新，请重新登录", 401)
            if account.must_change_password and not allow_initial:
                raise ServiceError("PASSWORD_CHANGE_REQUIRED", "请先修改初始密码", 403)
        if record.purpose != "login":
            context = context or self.context(record)
            if record.purpose == "management":
                member = await self.active_member(context, account=account)
                if member.revision != record.membership_version:
                    raise ServiceError("UNAUTHENTICATED", "成员授权已更新，请重新登录", 401)
            else:
                service = await self.service_identity(context)
            await self.validate_channel(
                context, ValidatedIdentity(account, member, service), governance=governance
            )
        return ValidatedIdentity(account, member, service)

    def context(self, record: TokenRecord, request_id: str | None = None) -> AuthContext:
        if record.purpose == "login" or record.environment is None:
            raise ServiceError("TOKEN_PURPOSE_INVALID", "请先进入渠道工作区", 401)
        return AuthContext(
            scope=Scope(
                channel_id=record.channel_id,
                environment=record.environment,
                data_scope_id=record.data_scope_id,
            ),
            principal_type=record.principal_type,
            principal_id=record.principal_id,
            actor_id=record.principal_id if record.principal_type == "management" else None,
            client_id=record.client_id,
            key_id=record.key_id,
            session_id=record.session_id,
            token_digest=record.token_digest,
            request_id=request_id or new_id("request"),
        )

    async def authenticate(self, bearer: str, purpose: str) -> AuthContext:
        record = await self.tokens.read(bearer, {purpose})
        await self.validate_record(record)
        record = await self.tokens.renew(record)
        return self.context(record)

    async def admin_session(
        self, bearer: str, request_id: str, *, allow_initial: bool = False, governance: bool = False
    ) -> AdminSession:
        record = await self.tokens.read(bearer, {"login", "management"})
        identity = await self.validate_record(
            record, allow_initial=allow_initial, governance=governance
        )
        account = identity.account
        if account is None:
            raise ServiceError("UNAUTHENTICATED", "管理身份不完整", 401)
        record = await self.tokens.renew(record)
        context: AuthContext | ControlAuthContext
        if record.purpose == "login":
            context = ControlAuthContext(
                scope=ControlScope(purpose="identity_lookup", actor_id=account.id),
                principal_id=account.id,
                request_id=request_id,
                session_id=record.session_id,
                token_digest=record.token_digest,
            )
        else:
            context = self.context(record, request_id)
        return AdminSession(record, account, context)

    async def revalidate(self, context: AuthContext) -> ValidatedIdentity:
        if context.actor_id and (context.client_id or context.key_id):
            raise ServiceError("UNAUTHENTICATED", "身份来源冲突", 401)
        if context.session_id or context.token_digest:
            if not context.session_id or not context.token_digest:
                raise ServiceError("UNAUTHENTICATED", "会话凭据不完整", 401)
            record = await self.tokens.read_digest(context.token_digest, {context.principal_type})
            if (
                record.session_id != context.session_id
                or record.channel_id != context.scope.channel_id
                or record.environment != context.scope.environment
                or record.principal_id != context.principal_id
                or record.client_id != context.client_id
                or record.key_id != context.key_id
                or (
                    record.purpose == "management"
                    and record.data_scope_id != context.scope.data_scope_id
                )
            ):
                raise ServiceError("UNAUTHENTICATED", "会话归属不符", 401)
            identity = await self.validate_record(record, context=context)
            await self.tokens.renew(record)
            return identity
        elif context.principal_type != "worker":
            raise ServiceError("UNAUTHENTICATED", "缺少会话凭据", 401)
        if context.actor_id:
            identity = ValidatedIdentity(member=await self.active_member(context))
        elif context.client_id and context.key_id:
            identity = ValidatedIdentity(service=await self.service_identity(context))
        else:
            raise ServiceError("UNAUTHENTICATED", "缺少原始身份来源", 401)
        await self.validate_channel(context, identity)
        return identity
