"""角色动作上限、资源授权、数据域及主体权限的交集。"""

from creativity_service.core.auth.authentication import AuthenticationService
from creativity_service.core.auth.types import (
    AccountState,
    AuthorizationDecision,
    GrantState,
    MembershipState,
    ResourceStateReader,
    SubjectAuthorityReader,
)
from creativity_service.core.context import AuthContext
from creativity_service.core.primitives import ServiceError, unavailable, utcnow
from creativity_service.modules.iam.roles import INDEPENDENT_ACTIONS, role_actions

SENSITIVE_ACTIONS = frozenset(
    {
        "evaluation:content",
        "run:content",
        "conversation:read",
        "memory:read",
        "snapshot:read",
        "artifact:download",
    }
)


def applicable(grant: GrantState, user_id: str, roles: list[str]) -> bool:
    return (grant.grantee_type == "account" and grant.grantee_id == user_id) or (
        grant.grantee_type == "role" and grant.grantee_id in roles
    )


def resource_covers(
    grant: GrantState, channel_id: str, resource_type: str, resource_id: str
) -> bool:
    return grant.channel_id == channel_id and (
        (grant.resource_type == "channel" and grant.resource_id == channel_id)
        or (grant.resource_type == "data_scope" and grant.resource_id in grant.data_scopes)
        or (grant.resource_type == resource_type and grant.resource_id in {"*", resource_id})
    )


def effective_actions(
    member: MembershipState,
    grants: list[GrantState],
    environment: str,
    data_scope_id: str,
    resource_type: str,
    resource_id: str,
) -> frozenset[str]:
    if (
        not member.roles
        or member.status != "ACTIVE"
        or environment not in member.environments
        or data_scope_id not in member.data_scopes
    ):
        return frozenset()
    allowed = frozenset(
        action
        for grant in grants
        if applicable(grant, member.user_id, member.roles)
        and environment in grant.environments
        and data_scope_id in grant.data_scopes
        and (grant.resource_type != "data_scope" or grant.resource_id == data_scope_id)
        and resource_covers(grant, member.channel_id, resource_type, resource_id)
        for action in grant.allowed_actions
    )
    return allowed & (role_actions(member.roles) | member.custom_actions | INDEPENDENT_ACTIONS)


def action_allowed(actions: frozenset[str], action: str) -> bool:
    if action not in actions:
        return False
    if action in SENSITIVE_ACTIONS and "data:read_sensitive" not in actions:
        return False
    if action == "artifact:download" and "data:export" not in actions:
        return False
    return True


def require_platform(account: AccountState, action: str) -> None:
    if account.status != "ACTIVE" or account.must_change_password:
        raise ServiceError("FORBIDDEN", "账号当前不能执行此操作", 403)
    if action not in role_actions(account.platform_roles):
        raise ServiceError("FORBIDDEN", "无权执行此操作", 403)


class IamAuthorization:
    def __init__(
        self,
        authentication: AuthenticationService,
        resources: ResourceStateReader | None = None,
        subjects: SubjectAuthorityReader | None = None,
    ) -> None:
        self.authentication, self.resources, self.subjects = authentication, resources, subjects

    async def verify_resource(
        self, context: AuthContext, resource_type: str, resource_id: str
    ) -> None:
        if resource_type == "channel":
            if resource_id != context.scope.channel_id:
                raise ServiceError("NOT_FOUND", "请求资源不存在", 404)
            return
        if resource_id in {"*", "new", "scope"}:
            return
        if self.resources is None:
            raise unavailable("资源当前状态服务")
        state = await self.resources.read_current(context, resource_type, resource_id)
        scope = context.scope
        if (
            state is None
            or state.resource_type != resource_type
            or state.resource_id != resource_id
            or state.scope.channel_id != scope.channel_id
            or state.scope.environment != scope.environment
            or (
                state.scope.data_scope_id is not None
                and state.scope.data_scope_id != scope.data_scope_id
            )
            or (
                state.scope.subject_type is not None
                and (state.scope.subject_type, state.scope.subject_id)
                != (scope.subject_type, scope.subject_id)
            )
        ):
            raise ServiceError("NOT_FOUND", "请求资源不存在", 404)
        if not state.active:
            raise ServiceError("RESOURCE_DISABLED", "资源已停用", 403)

    async def check(
        self, context: AuthContext, action: str, resource_type: str, resource_id: str
    ) -> AuthorizationDecision:
        await self.authentication.revalidate(context)
        await self.verify_resource(context, resource_type, resource_id)
        if context.actor_id:
            member = await self.authentication.active_member(context)
            grants = await self.authentication.identities.grants(context.scope.channel_id)
            actions = effective_actions(
                member,
                grants,
                context.scope.environment,
                context.scope.data_scope_id or "",
                resource_type,
                resource_id,
            )
        else:
            identity = await self.authentication.service_identity(context)
            if self.subjects is None:
                raise unavailable("主体委托授权服务")
            subject = await self.subjects.read_current(context)
            if (
                subject.scope != context.scope
                or subject.expires_at <= utcnow()
                or not context.scope.subject_type
                or not context.scope.subject_id
            ):
                raise ServiceError("UNAUTHENTICATED", "主体委托已失效", 401)
            resources = subject.resources.get(resource_type, frozenset())
            actions = (
                identity.client_actions
                & identity.key_actions
                & subject.actions
                & subject.agent_actions
                if resource_id in resources or "*" in resources
                else frozenset()
            )
        filtered = sorted(value for value in actions if action_allowed(actions, value))
        allowed = action in filtered
        return AuthorizationDecision(
            allowed=allowed, actions=filtered, reason=None if allowed else "无权执行此操作"
        )

    async def require(self, context: AuthContext, action: str, resource_id: str) -> None:
        resource_type = "version" if action == "release:publish" else action.split(":", 1)[0]
        decision = await self.check(context, action, resource_type, resource_id)
        if not decision.allowed:
            raise ServiceError("FORBIDDEN", decision.reason or "无权执行此操作", 403)

    async def boundary(
        self, context: AuthContext, action: str, resource_type: str, resource_id: str
    ) -> None:
        """SSE 发送、心跳、文件交付及运行外部调用共用此入口。"""
        if not (await self.check(context, action, resource_type, resource_id)).allowed:
            raise ServiceError("FORBIDDEN", "当前授权已撤销", 403)
