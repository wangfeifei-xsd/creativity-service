"""角色动作上限、资源授权、环境及主体权限的交集。"""

from dataclasses import dataclass

from creativity_service.core.auth.authentication import AuthenticationService
from creativity_service.core.auth.types import (
    AccountState,
    AuthorizationDecision,
    GrantState,
    MembershipState,
    ResourceState,
    ResourceStateReader,
    ServiceIdentity,
    SubjectAuthority,
    SubjectAuthorityReader,
)
from creativity_service.core.context import AuthContext
from creativity_service.core.primitives import ServiceError, digest, unavailable, utcnow
from creativity_service.modules.iam.roles import INDEPENDENT_ACTIONS

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
        or (grant.resource_type == resource_type and grant.resource_id in {"*", resource_id})
    )


def effective_actions(
    member: MembershipState,
    grants: list[GrantState],
    environment: str,
    resource_type: str,
    resource_id: str,
) -> frozenset[str]:
    if not member.roles or member.status != "ACTIVE" or environment not in member.environments:
        return frozenset()
    automatic_grants = {
        "initial_" + member.id,
        "administrator_" + digest([member.channel_id, member.user_id])[:40],
    }
    allowed = frozenset(
        action
        for grant in grants
        if applicable(grant, member.user_id, member.roles)
        and environment in grant.environments
        and resource_covers(grant, member.channel_id, resource_type, resource_id)
        for action in grant.allowed_actions
        # 自动分配的发布权受当前角色约束；独立资源授权继续按显式授权生效。
        if action != "release:publish"
        or grant.id not in automatic_grants
        or action in member.custom_actions
    )
    return allowed & (member.custom_actions | INDEPENDENT_ACTIONS)


def action_allowed(actions: frozenset[str], action: str) -> bool:
    if action not in actions:
        return False
    if action in SENSITIVE_ACTIONS and "data:read_sensitive" not in actions:
        return False
    if action == "artifact:download" and "data:export" not in actions:
        return False
    return True


def channel_run_actions(
    member: MembershipState, grants: list[GrantState], environment: str
) -> frozenset[str]:
    """当前渠道管理权包含运行内容读取，不授予其他敏感原文或导出权限。"""
    actions = effective_actions(member, grants, environment, "channel", member.channel_id)
    return frozenset({"run:read", "run:content"}) if "channel:manage" in actions else frozenset()


def platform_actions(account: AccountState) -> frozenset[str]:
    return account.custom_actions


def require_platform(account: AccountState, action: str) -> None:
    if account.status != "ACTIVE" or account.must_change_password:
        raise ServiceError("FORBIDDEN", "账号当前不能执行此操作", 403)
    if action not in platform_actions(account):
        raise ServiceError("FORBIDDEN", "无权执行此操作", 403)


def verify_resource_state(
    context: AuthContext, resource_type: str, resource_id: str, state: ResourceState | None
) -> None:
    """校验可信批量查询结果的归属；此原子校验不再访问数据库。"""
    if resource_type == "channel":
        if resource_id != context.scope.channel_id:
            raise ServiceError("NOT_FOUND", "请求资源不存在", 404)
        return
    if resource_id in {"*", "new", "scope"}:
        return
    scope = context.scope
    if (
        state is None
        or state.resource_type != resource_type
        or state.resource_id != resource_id
        or state.scope.channel_id != scope.channel_id
        or state.scope.environment != scope.environment
        or (
            state.scope.subject_type is not None
            and (state.scope.subject_type, state.scope.subject_id)
            != (scope.subject_type, scope.subject_id)
        )
    ):
        raise ServiceError("NOT_FOUND", "请求资源不存在", 404)
    if not state.active:
        raise ServiceError("RESOURCE_DISABLED", "资源已停用", 403)


@dataclass(frozen=True)
class ReadAuthorization:
    """当前读取操作的授权基础数据，不得传入写入事务或后续执行边界。"""

    context: AuthContext
    member: MembershipState | None = None
    grants: tuple[GrantState, ...] = ()
    service: ServiceIdentity | None = None
    subject: SubjectAuthority | None = None

    def resource_ids(self, resource_type: str, action: str) -> frozenset[str] | None:
        """把资源授权收窄为查询标识；None 表示当前范围内可读全部资源。"""
        if action in self.actions(resource_type, "*"):
            return None
        candidates = (
            {g.resource_id for g in self.grants if g.resource_type == resource_type}
            if self.member is not None
            else set(self.subject.resources.get(resource_type, ()))
            if self.subject
            else set()
        )
        return frozenset(
            identifier
            for identifier in candidates
            if identifier not in {"*", "new", "scope"}
            and action
            in self.actions(
                resource_type,
                identifier,
                ResourceState(
                    scope=self.context.scope,
                    resource_type=resource_type,
                    resource_id=identifier,
                    name="",
                    active=True,
                ),
            )
        )

    def actions(
        self,
        resource_type: str,
        resource_id: str,
        state: ResourceState | None = None,
        *,
        context: AuthContext | None = None,
    ) -> frozenset[str]:
        target = context or self.context
        origin = self.context
        # 管理列表可收窄到一条记录的主体，不能换身份、请求、渠道或环境。
        if (
            target.model_dump(exclude={"scope"}) != origin.model_dump(exclude={"scope"})
            or target.scope.model_dump(exclude={"subject_type", "subject_id"})
            != origin.scope.model_dump(exclude={"subject_type", "subject_id"})
            or ((origin.scope.subject_id or not origin.actor_id) and target.scope != origin.scope)
        ):
            raise ServiceError("SCOPE_MISMATCH", "读取授权不能扩大身份或范围", 403)
        verify_resource_state(target, resource_type, resource_id, state)
        if self.member is not None:
            actions = effective_actions(
                self.member,
                list(self.grants),
                target.scope.environment,
                resource_type,
                resource_id,
            )
        elif self.service is not None and self.subject is not None:
            if self.service.expires_at <= utcnow() or self.subject.expires_at <= utcnow():
                raise ServiceError("UNAUTHENTICATED", "主体委托已失效", 401)
            resources = self.subject.resources.get(resource_type, frozenset())
            actions = (
                self.service.client_actions
                & self.service.key_actions
                & self.subject.actions
                & self.subject.agent_actions
                if resource_id in resources or "*" in resources
                else frozenset()
            )
        else:
            raise unavailable("当前身份授权数据")
        allowed = frozenset(value for value in actions if action_allowed(actions, value))
        if self.member is not None and resource_type == "run":
            # 渠道、环境、资源归属已核验；只对当前有效成员应用渠道管理权限。
            allowed |= channel_run_actions(self.member, list(self.grants), target.scope.environment)
        return allowed


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
    ) -> ResourceState | None:
        state = None
        if resource_type != "channel" and resource_id not in {"*", "new", "scope"}:
            if self.resources is None:
                raise unavailable("资源当前状态服务")
            state = await self.resources.read_current(context, resource_type, resource_id)
        verify_resource_state(context, resource_type, resource_id, state)
        return state

    async def read_policy(self, context: AuthContext) -> ReadAuthorization:
        """一次复核并返回可复用的数据；每次调用都读取当前状态，不做跨请求缓存。"""
        read_scope = getattr(self.authentication.identities, "read_scope", None)
        # 管理身份在 Redis 核对后只访问本地数据库；带远端主体复核的业务身份不持此连接。
        if context.actor_id and read_scope:
            async with read_scope():
                return await self.load_policy(context)
        return await self.load_policy(context)

    async def load_policy(self, context: AuthContext) -> ReadAuthorization:
        identity = await self.authentication.revalidate(context)
        if identity.member is not None:
            grants = await self.authentication.identities.grants(context.scope.channel_id)
            return ReadAuthorization(context, member=identity.member, grants=tuple(grants))
        if identity.service is None or self.subjects is None:
            raise unavailable("主体委托授权服务")
        subject = await self.subjects.read_current(context)
        if (
            subject.scope != context.scope
            or subject.expires_at <= utcnow()
            or not context.scope.subject_type
            or not context.scope.subject_id
        ):
            raise ServiceError("UNAUTHENTICATED", "主体委托已失效", 401)
        return ReadAuthorization(context, service=identity.service, subject=subject)

    async def allowed_actions(
        self, context: AuthContext, resource_type: str, resource_id: str
    ) -> frozenset[str]:
        policy = await self.read_policy(context)
        state = await self.verify_resource(context, resource_type, resource_id)
        return policy.actions(resource_type, resource_id, state)

    async def check(
        self, context: AuthContext, action: str, resource_type: str, resource_id: str
    ) -> AuthorizationDecision:
        filtered = sorted(await self.allowed_actions(context, resource_type, resource_id))
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
