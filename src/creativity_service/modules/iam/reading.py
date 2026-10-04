"""查询响应使用的无数据库装配原子能力。"""

from collections.abc import Mapping, Sequence
from typing import Any

from creativity_service.core.auth.types import ResourceState
from creativity_service.core.context import AuthContext, Authorization
from creativity_service.core.contracts import VisibleAction
from creativity_service.core.primitives import ServiceError
from creativity_service.modules.iam.authorization import IamAuthorization, ReadAuthorization


async def read_policy(
    authorization: Authorization, context: AuthContext
) -> ReadAuthorization | None:
    """资源适配器沿用底层 IAM 的读取结果，独立模块仍可注入授权端口。"""
    root = getattr(authorization, "authorization", authorization)
    return await root.read_policy(context) if isinstance(root, IamAuthorization) else None


async def read_actions(
    authorization: Authorization,
    context: AuthContext,
    kind: str,
    identifier: str,
    requested: Sequence[str],
    *,
    policy: ReadAuthorization | None = None,
    state: ResourceState | None = None,
) -> frozenset[str]:
    if policy is not None:
        return policy.actions(kind, identifier, state, context=context)
    root = getattr(authorization, "authorization", authorization)
    if isinstance(root, IamAuthorization):
        return await root.allowed_actions(context, kind, identifier)
    allowed = set()
    for action in dict.fromkeys(requested):
        try:
            await authorization.require(context, action, identifier)
        except ServiceError as exc:
            if exc.status not in {403, 404}:
                raise
        else:
            allowed.add(action)
    return frozenset(allowed)


def resource_state(
    context: AuthContext, kind: str, row: Mapping[str, Any], *, active: bool = True
) -> ResourceState:
    """调用方须传入按该上下文读取的记录，跨主体列表须先建立记录自身的上下文。"""
    return ResourceState(
        scope=context.scope,
        resource_type=kind,
        resource_id=row["id"],
        name=row.get("name") or row.get("title") or row.get("display_name") or "",
        active=active,
    )


def visible_actions(
    allowed: frozenset[str], values: Sequence[tuple[str, str, str]]
) -> list[VisibleAction]:
    return [
        VisibleAction(action_key=key, label=label)
        for key, label, permission in values
        if permission in allowed
    ]


def require_action(allowed: frozenset[str], action: str) -> None:
    if action not in allowed:
        raise ServiceError("FORBIDDEN", "无权执行此操作", 403)
