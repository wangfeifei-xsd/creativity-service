"""外部身份复核与锁下本地授权分离，发布事务不访问 Redis 或网络。"""

from dataclasses import dataclass

from creativity_service.core.auth.types import GrantState, MembershipState, ServiceIdentity
from creativity_service.core.context import AuthContext
from creativity_service.core.database import UnitOfWork
from creativity_service.core.primitives import ServiceError, utcnow
from creativity_service.integrations.business.delegation import DelegationClaims
from creativity_service.modules.agents.repositories import repository
from creativity_service.modules.channels.state import current_service
from creativity_service.modules.iam.authorization import action_allowed, effective_actions
from creativity_service.modules.iam.repositories import (
    membership_state,
    one,
    policy_key,
    rows,
    to_state,
)
from creativity_service.modules.integrations.repositories import (
    configuration_key,
    environment_scope,
    source_mapping,
)
from creativity_service.modules.integrations.repositories import (
    repository as integration_repository,
)


@dataclass(frozen=True)
class LockedAuthorization:
    """锁内读取一次授权；仅可在当前事务和已验证范围内复用。"""

    uow: UnitOfWork
    context: AuthContext
    member: MembershipState | None = None
    grants: tuple[GrantState, ...] = ()
    identity: ServiceIdentity | None = None
    claims: DelegationClaims | None = None

    def require(self, context: AuthContext, action: str, kind: str, identifier: str) -> None:
        self.uow.require_lock(policy_key(context.scope.channel_id))
        origin = self.context
        if (
            context.model_dump(exclude={"scope"}) != origin.model_dump(exclude={"scope"})
            or context.scope.model_dump(exclude={"subject_type", "subject_id"})
            != origin.scope.model_dump(exclude={"subject_type", "subject_id"})
            or ((origin.scope.subject_id or not origin.actor_id) and context.scope != origin.scope)
        ):
            raise ServiceError("SCOPE_MISMATCH", "锁内授权不能扩大身份或范围", 403)
        if self.member is not None:
            actions = effective_actions(
                self.member,
                list(self.grants),
                context.scope.environment,
                context.scope.data_scope_id or "",
                kind,
                identifier,
            )
            allowed = action_allowed(actions, action)
        elif self.identity is not None and self.claims is not None:
            actions = (
                self.identity.client_actions & self.identity.key_actions & set(self.claims.actions)
            )
            allowed = action in actions and bool(
                {identifier, "*"} & set(self.claims.resources.get(kind, []))
            )
        else:
            allowed = False
        if not allowed:
            raise ServiceError("FORBIDDEN", "当前资源或目标环境授权不足", 403)


async def locked_policy(uow: UnitOfWork, context: AuthContext) -> LockedAuthorization:
    scope = context.scope
    uow.require_lock(policy_key(scope.channel_id))
    uow.require_lock(policy_key("system"))
    channel = await repository("channels", scope).get(uow.connection, scope.channel_id)
    environments = await repository("channel_environments", scope).find(uow.connection)
    if (
        not channel
        or channel["status"] != "ACTIVE"
        or len(environments) != 1
        or environments[0]["status"] != "ACTIVE"
    ):
        raise ServiceError("FORBIDDEN", "渠道或目标环境不可用", 403)
    if scope.data_scope_id:
        domain = await repository("data_scopes", scope).get(uow.connection, scope.data_scope_id)
        if not domain or domain["status"] != "ACTIVE":
            raise ServiceError("FORBIDDEN", "当前业务数据域不可用", 403)
    if context.token_digest and await rows(
        uow.connection,
        "iam_revocations",
        scope.channel_id,
        kind="token",
        target_id=context.token_digest,
    ):
        raise ServiceError("UNAUTHENTICATED", "当前会话已撤销", 401)
    if context.actor_id:
        account = await one(uow.connection, "platform_accounts", "system", id=context.actor_id)
        member = await one(
            uow.connection, "channel_memberships", scope.channel_id, user_id=context.actor_id
        )
        if (
            not account
            or account["status"] != "ACTIVE"
            or account["must_change_password"]
            or not member
        ):
            raise ServiceError("FORBIDDEN", "当前账号或渠道成员不可用", 403)
        grants = [
            to_state(GrantState, row)
            for row in await rows(uow.connection, "resource_grants", scope.channel_id)
        ]
        return LockedAuthorization(
            uow,
            context,
            member=await membership_state(uow.connection, member),
            grants=tuple(grants),
        )
    elif context.client_id and context.key_id:
        uow.require_lock(configuration_key(scope))
        identity = await current_service(uow.connection, context)
        delegation = await integration_repository(
            environment_scope(scope), "delegation_nonces"
        ).get(uow.connection, context.delegation_id or "")
        if (
            not delegation
            or delegation["client_id"] != context.client_id
            or delegation["resolved_scope"] != scope.model_dump()
            # Worker 在事务外已复核源端当前权限，原请求声明自然到期不撤销已受理任务。
            or (context.principal_type != "worker" and delegation["expires_at"] <= utcnow())
        ):
            raise ServiceError("FORBIDDEN", "业务主体委托已失效", 403)
        key = await integration_repository(environment_scope(scope), "delegation_keys").get(
            uow.connection, delegation["kid"]
        )
        if (
            not key
            or key["status"] != "ACTIVE"
            or key["expires_at"] <= utcnow()
            or key["not_before"] > utcnow()
        ):
            raise ServiceError("FORBIDDEN", "业务主体委托凭据已撤销", 403)
        claims = DelegationClaims.model_validate(delegation["claims"])
        domain = await source_mapping(
            uow.connection, environment_scope(scope), claims.data_scope.type, claims.data_scope.id
        )
        if domain["id"] != scope.data_scope_id:
            raise ServiceError("FORBIDDEN", "当前业务主体未获此资源授权", 403)
        return LockedAuthorization(uow, context, identity=identity, claims=claims)
    else:
        raise ServiceError("FORBIDDEN", "此操作需要管理身份", 403)


async def locked_require(
    uow: UnitOfWork, context: AuthContext, action: str, kind: str, identifier: str
) -> None:
    (await locked_policy(uow, context)).require(context, action, kind, identifier)
