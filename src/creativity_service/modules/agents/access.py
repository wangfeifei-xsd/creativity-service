"""外部身份复核与锁下本地授权分离，发布事务不访问 Redis 或网络。"""

from creativity_service.core.auth.types import GrantState, MembershipState
from creativity_service.core.context import AuthContext
from creativity_service.core.database import UnitOfWork
from creativity_service.core.primitives import ServiceError, utcnow
from creativity_service.integrations.business.delegation import DelegationClaims
from creativity_service.modules.agents.repositories import repository
from creativity_service.modules.channels.state import current_service
from creativity_service.modules.iam.authorization import action_allowed, effective_actions
from creativity_service.modules.iam.repositories import one, policy_key, rows, to_state
from creativity_service.modules.integrations.repositories import (
    configuration_key,
    environment_scope,
    source_mapping,
)
from creativity_service.modules.integrations.repositories import (
    repository as integration_repository,
)


async def locked_require(
    uow: UnitOfWork, context: AuthContext, action: str, kind: str, identifier: str
) -> None:
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
        actions = effective_actions(
            to_state(MembershipState, member),
            grants,
            scope.environment,
            scope.data_scope_id or "",
            kind,
            identifier,
        )
        if not action_allowed(actions, action):
            raise ServiceError("FORBIDDEN", "当前资源或目标环境授权不足", 403)
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
            or delegation["expires_at"] <= utcnow()
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
        resources = claims.resources.get(kind, [])
        actions = identity.client_actions & identity.key_actions & set(claims.actions)
        if (
            domain["id"] != scope.data_scope_id
            or action not in actions
            or not ({identifier, "*"} & set(resources))
        ):
            raise ServiceError("FORBIDDEN", "当前业务主体未获此资源授权", 403)
    else:
        raise ServiceError("FORBIDDEN", "此操作需要管理身份", 403)
