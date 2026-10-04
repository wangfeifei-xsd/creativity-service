"""管理写入在渠道授权互斥下复核最新权限，事务内仅查询数据库。"""

from creativity_service.core.auth.types import GrantState
from creativity_service.core.context import AuthContext
from creativity_service.core.database import UnitOfWork
from creativity_service.core.primitives import ServiceError
from creativity_service.modules.channels.repositories import required
from creativity_service.modules.channels.state import require_available
from creativity_service.modules.iam.authorization import ReadAuthorization, effective_actions
from creativity_service.modules.iam.repositories import (
    membership_state,
    one,
    policy_key,
    rows,
    to_state,
)


async def management_policy(uow: UnitOfWork, context: AuthContext) -> ReadAuthorization:
    scope = context.scope
    uow.require_lock(policy_key(scope.channel_id))
    uow.require_lock(policy_key("system"))
    if context.principal_type not in {"management", "worker"} or not context.actor_id:
        raise ServiceError("FORBIDDEN", "此操作需要管理身份", 403)
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
        raise ServiceError("FORBIDDEN", "当前管理身份不可用", 403)
    channel = await required(uow.connection, "channels", scope.channel_id, id=scope.channel_id)
    env = await required(
        uow.connection, "channel_environments", scope.channel_id, environment=scope.environment
    )
    require_available(channel, env)
    grants = [
        to_state(GrantState, row)
        for row in await rows(uow.connection, "resource_grants", scope.channel_id)
    ]
    return ReadAuthorization(
        context, member=await membership_state(uow.connection, member), grants=tuple(grants)
    )


async def require_management(
    uow: UnitOfWork, context: AuthContext, action: str, data_scopes: list[str] | None = None
) -> None:
    if context.principal_type != "management":
        raise ServiceError("FORBIDDEN", "此操作需要管理身份", 403)
    policy = await management_policy(uow, context)
    scope = context.scope
    assert policy.member is not None
    for domain in data_scopes or [scope.data_scope_id or ""]:
        if action not in effective_actions(
            policy.member,
            list(policy.grants),
            scope.environment,
            domain,
            "channel",
            scope.channel_id,
        ):
            raise ServiceError("FORBIDDEN", "无权管理此业务范围", 403)
        value = await required(
            uow.connection,
            "data_scopes",
            scope.channel_id,
            id=domain,
            environment=scope.environment,
        )
        if value["status"] != "ACTIVE":
            raise ServiceError("DATA_SCOPE_DISABLED", "业务数据域不可用", 403)
