"""外部身份复核与锁下本地授权分离，发布事务不访问 Redis 或网络。"""

from creativity_service.core.auth.types import GrantState, MembershipState
from creativity_service.core.context import AuthContext
from creativity_service.core.database import UnitOfWork
from creativity_service.core.primitives import ServiceError, utcnow
from creativity_service.modules.agents.repositories import repository
from creativity_service.modules.iam.authorization import action_allowed, effective_actions
from creativity_service.modules.iam.repositories import one, policy_key, rows, to_state


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
    elif action == "run:create" and context.client_id and context.key_id:
        client = await repository("service_clients", scope).get(uow.connection, context.client_id)
        key = await repository("channel_keys", scope).get(uow.connection, context.key_id)
        if (
            not client
            or not key
            or client["status"] != "ACTIVE"
            or key["status"] != "ACTIVE"
            or key["expires_at"] <= utcnow()
        ):
            raise ServiceError("FORBIDDEN", "调用服务或凭据已停用", 403)
        if (
            action not in set(client["scopes"]) & set(key["scopes"])
            or scope.data_scope_id not in client["data_scopes"]
        ):
            raise ServiceError("FORBIDDEN", "调用服务授权不足", 403)
    else:
        raise ServiceError("FORBIDDEN", "此操作需要管理身份", 403)
