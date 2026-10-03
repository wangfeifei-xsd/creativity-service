"""登录与工作区签发基础；渠道目录和当前状态由方案 05 注入。"""

from creativity_service.core.auth.authentication import AdminSession, AuthenticationService
from creativity_service.core.auth.passwords import PasswordHasher, normalize_login
from creativity_service.core.auth.types import TokenResponse, WorkspaceDirectory, WorkspaceOption
from creativity_service.core.context import AuthContext, ControlScope, Scope, require_channel_state
from creativity_service.core.contracts import NavigationItem, VisibleAction
from creativity_service.core.database import transaction
from creativity_service.core.locking import record_key
from creativity_service.core.primitives import ServiceError, digest, new_id, unavailable
from creativity_service.modules.iam.accounts import current_actor
from creativity_service.modules.iam.audit import append_event
from creativity_service.modules.iam.authorization import (
    IamAuthorization,
    action_allowed,
    effective_actions,
)
from creativity_service.modules.iam.repositories import IdentityRepository, one, policy_key
from creativity_service.modules.iam.revocations import RevocationService, enqueue
from creativity_service.modules.iam.roles import ACTION_NAMES, GOVERNANCE_ACTIONS, role_actions
from creativity_service.modules.iam.schemas import (
    ChannelContextInput,
    LoginInput,
    SessionView,
    UserView,
)

NAVIGATION = (
    ("model-providers", "模型供应商", "channel:govern"),
    ("models", "模型配置", "model:manage"),
    ("model-routes", "模型路由", "model:manage"),
    ("accounts", "账号管理", "account:manage"),
    ("channels", "渠道管理", "channel:govern"),
    ("members", "成员与权限", "membership:read"),
    ("resource-grants", "资源授权", "grant:read"),
    ("audit-events", "操作审计", "audit:read"),
    ("agents", "智能体", "agent:manage"),
    ("evaluations", "效果评测", "evaluation:read"),
    ("prompts", "提示词", "prompt:manage"),
    ("tools", "工具", "tool:manage"),
    ("integrations", "业务接入", "integration:manage"),
    ("skills", "技能管理", "skill:manage"),
    ("mcp-connections", "MCP 连接", "mcp:manage"),
    ("runs", "运行记录", "run:read"),
    ("conversations", "会话管理", "conversation:read"),
    ("memories", "记忆管理", "memory:read"),
    ("usage", "用量", "usage:read"),
)


class SessionService:
    def __init__(
        self,
        repository: IdentityRepository,
        authentication: AuthenticationService,
        authorization: IamAuthorization,
        passwords: PasswordHasher,
        revocations: RevocationService,
        directory: WorkspaceDirectory | None = None,
    ) -> None:
        self.repository, self.authentication, self.authorization = (
            repository,
            authentication,
            authorization,
        )
        self.passwords, self.revocations, self.directory = passwords, revocations, directory

    async def login(self, body: LoginInput, remote_ip: str, request_id: str) -> TokenResponse:
        name = normalize_login(body.login_name)
        await self.authentication.tokens.limit_login(name, remote_ip)
        candidate = await self.repository.credentials(name)
        verified = await self.passwords.verify(
            body.password.get_secret_value(), candidate["password_hash"] if candidate else None
        )
        event_id = new_id("audit")
        failure: ServiceError | None = None
        account = None
        async with transaction(
            self.repository.engine,
            ControlScope(purpose="identity_lookup", actor_id="login_service"),
            [policy_key("system"), record_key("system", "audit_events", event_id)],
        ) as uow:
            current = await one(uow.connection, "platform_accounts", "system", login_name=name)
            if (
                not verified
                or candidate is None
                or current is None
                or current["credential_version"] != candidate["credential_version"]
                or current["password_hash"] != candidate["password_hash"]
            ):
                failure = ServiceError("LOGIN_FAILED", "登录名或密码不正确", 401)
            elif current["status"] != "ACTIVE":
                failure = ServiceError("ACCOUNT_DISABLED", "账号已停用", 401)
            else:
                account = current
            await append_event(
                uow,
                event_id,
                account["id"] if account else "unauthenticated",
                request_id,
                "auth:login",
                "account",
                account["id"] if account else digest(name),
                outcome="DENIED" if failure else "SUCCEEDED",
            )
        if failure:
            raise failure
        assert account is not None
        response, record = await self.authentication.tokens.issue(
            purpose="login",
            principal_id=account["id"],
            credential_version=account["credential_version"],
            must_change_password=account["must_change_password"],
        )
        # 签发不跨数据库事务；提交后发生的重置也会在返回凭据前再次被发现。
        await self.authentication.validate_record(record, allow_initial=True)
        return response

    async def logout(self, session: AdminSession) -> None:
        await self.authentication.revalidate_admin(session, allow_initial=True, governance=True)
        channel_id = session.token.channel_id
        scope = session.context.scope
        event_id, revoke_id = new_id("audit"), new_id("revoke")
        async with transaction(
            self.repository.engine,
            scope,
            [
                policy_key("system"),
                policy_key(channel_id),
                record_key(channel_id, "audit_events", event_id),
                record_key(channel_id, "iam_revocations", revoke_id),
            ],
        ) as uow:
            await current_actor(uow, session, None)
            item = await enqueue(uow, revoke_id, "token", session.token.token_digest)
            await append_event(
                uow,
                event_id,
                session.account.id,
                session.context.request_id,
                "auth:logout",
                "account",
                session.account.id,
            )
        await self.revocations.complete(item)

    async def channels(self, session: AdminSession) -> list[WorkspaceOption]:
        await self.authentication.revalidate_admin(session, governance=True)
        await self.authentication.active_account(session.account.id)
        if self.directory is None:
            raise unavailable("渠道工作区目录")
        result = []
        for option in await self.directory.list_for(session.account.id):
            member = await self.repository.membership(option.channel_id, session.account.id)
            if (
                member is None
                or member.status != "ACTIVE"
                or option.environment not in member.environments
                or option.data_scope_id not in member.data_scopes
            ):
                continue
            grants = await self.repository.grants(option.channel_id)
            if not any(
                effective_actions(
                    member,
                    grants,
                    option.environment,
                    option.data_scope_id,
                    grant.resource_type,
                    grant.resource_id,
                )
                for grant in grants
            ):
                continue
            context = AuthContext(
                scope=Scope(
                    channel_id=option.channel_id,
                    environment=option.environment,
                    data_scope_id=option.data_scope_id,
                ),
                principal_type="management",
                principal_id=session.account.id,
                actor_id=session.account.id,
                request_id=session.context.request_id,
            )
            try:
                allowed = effective_actions(
                    member,
                    grants,
                    option.environment,
                    option.data_scope_id,
                    "channel",
                    option.channel_id,
                )
                await require_channel_state(
                    context,
                    self.authentication.channels,
                    governance=bool(allowed & GOVERNANCE_ACTIONS),
                )
            except ServiceError as exc:
                if exc.status in {401, 403, 404}:
                    continue
                raise
            if option not in result:
                result.append(option)
        return result

    async def enter(self, session: AdminSession, body: ChannelContextInput) -> TokenResponse:
        await self.authentication.revalidate_admin(session, governance=True)
        options = await self.channels(session)
        if not any(
            (o.channel_id, o.environment, o.data_scope_id)
            == (body.channel_id, body.environment, body.data_scope_id)
            for o in options
        ):
            raise ServiceError("NOT_FOUND", "可进入的工作区不存在", 404)
        member = await self.repository.membership(body.channel_id, session.account.id)
        account = await self.authentication.active_account(session.account.id)
        if account.credential_version != session.token.credential_version:
            raise ServiceError("UNAUTHENTICATED", "凭据已更新，请重新登录", 401)
        if member is None or member.status != "ACTIVE":
            raise ServiceError("MEMBERSHIP_DISABLED", "渠道成员不可用", 401)
        # 05 负责渠道目录与状态，Redis 脚本原子替换旧 Token，绝不修改既有运行的 Scope。
        response, record = await self.authentication.tokens.issue(
            purpose="management",
            principal_id=account.id,
            channel_id=body.channel_id,
            environment=body.environment,
            data_scope_id=body.data_scope_id,
            credential_version=account.credential_version,
            membership_version=member.revision,
            replace=session.token,
        )
        await self.authentication.validate_record(record, governance=True)
        event_id = new_id("audit")
        scope = Scope(
            channel_id=body.channel_id,
            environment=body.environment,
            data_scope_id=body.data_scope_id,
        )
        async with transaction(
            self.repository.engine, scope, [record_key(body.channel_id, "audit_events", event_id)]
        ) as uow:
            await append_event(
                uow,
                event_id,
                account.id,
                session.context.request_id,
                "auth:channel-context",
                "channel",
                body.channel_id,
            )
        return response

    async def enter_platform(self, session: AdminSession) -> TokenResponse:
        """返回系统登录范围并撤销旧工作区；平台操作仍逐接口验证授权。"""
        await self.authentication.revalidate_admin(session, governance=True)
        account = await self.authentication.active_account(session.account.id)
        response, record = await self.authentication.tokens.issue(
            purpose="login",
            principal_id=account.id,
            channel_id="system",
            credential_version=account.credential_version,
            replace=session.token,
        )
        await self.authentication.validate_record(record, governance=True)
        event_id = new_id("audit")
        async with transaction(
            self.repository.engine,
            ControlScope(purpose="identity_lookup", actor_id=account.id),
            [record_key("system", "audit_events", event_id)],
        ) as uow:
            await append_event(
                uow,
                event_id,
                account.id,
                session.context.request_id,
                "auth:platform-context",
                "account",
                account.id,
            )
        return response

    async def issue_service(self, context: AuthContext) -> TokenResponse:
        """05 验证 Key 后调用，不能直接暴露给客户端构造上下文。"""
        if context.principal_type != "service" or context.scope.subject_id is not None:
            raise ServiceError("TOKEN_PURPOSE_INVALID", "接入 Token 身份不正确", 401)
        identity = await self.authentication.service_identity(context)
        await require_channel_state(context, self.authentication.channels)
        response, record = await self.authentication.tokens.issue(
            purpose="service",
            channel_id=context.scope.channel_id,
            environment=context.scope.environment,
            principal_id=identity.client_id,
            client_id=identity.client_id,
            key_id=identity.key_id,
            expires_by=identity.expires_at,
        )
        await self.authentication.validate_record(record)
        return response

    async def view(self, session: AdminSession) -> SessionView:
        await self.authentication.revalidate_admin(session, governance=True)
        context = session.context
        workspace = None
        if isinstance(context, AuthContext):
            member = await self.authentication.active_member(context)
            grants = await self.repository.grants(context.scope.channel_id)
            actions = frozenset().union(
                *(
                    frozenset(a for a in effective if action_allowed(effective, a))
                    for grant in grants
                    for effective in [
                        effective_actions(
                            member,
                            grants,
                            context.scope.environment,
                            context.scope.data_scope_id or "",
                            grant.resource_type,
                            grant.resource_id,
                        )
                    ]
                )
            )
            options = await self.channels(session)
            workspace = next(
                (
                    o
                    for o in options
                    if (o.channel_id, o.environment, o.data_scope_id)
                    == (
                        context.scope.channel_id,
                        context.scope.environment,
                        context.scope.data_scope_id,
                    )
                ),
                None,
            )
            if workspace is None:
                raise ServiceError("FORBIDDEN", "当前工作区授权已失效", 403)
            state = await require_channel_state(
                context, self.authentication.channels, governance=True
            )
            if not (state.channel_active and state.environment_active and state.data_scope_active):
                actions &= GOVERNANCE_ACTIONS | {"audit:read", "usage:read"}
        else:
            account = await self.authentication.active_account(session.account.id)
            actions = role_actions(account.platform_roles)
        return SessionView(
            user=UserView(
                user_id=session.account.id,
                display_name=session.account.display_name,
                login_name=session.account.login_name,
            ),
            navigation=[
                NavigationItem(navigation_key=key, label=label)
                for key, label, action in NAVIGATION
                if action in actions
                or (key == "channels" and "channel:manage" in actions)
                or (key == "prompts" and "version:read" in actions)
            ],
            actions=[
                VisibleAction(action_key=action, label=ACTION_NAMES[action])
                for action in sorted(actions)
            ],
            workspace=workspace,
            expires_at=session.token.expires_at,
        )
