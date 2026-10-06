"""账号管理中的渠道多选授权；渠道成员仍是唯一权限依据。"""

from collections.abc import Sequence
from typing import Any

from sqlalchemy import and_, select
from sqlalchemy.ext.asyncio import AsyncConnection

from creativity_service.core.auth.types import AccountState
from creativity_service.core.context import Scope
from creativity_service.core.database import UnitOfWork
from creativity_service.core.locking import ResourceKey, record_key
from creativity_service.core.primitives import ServiceError, digest
from creativity_service.modules.channels.repositories import management_scope_id
from creativity_service.modules.channels.tables import metadata as channel_metadata
from creativity_service.modules.iam.audit import append_event, audit_ranges
from creativity_service.modules.iam.authorization import require_platform
from creativity_service.modules.iam.repositories import TABLES, membership_id, policy_key, save
from creativity_service.modules.iam.roles import ORDINARY_CHANNEL_ACTIONS


def administrator_grant_id(channel_id: str, user_id: str) -> str:
    return "administrator_" + digest([channel_id, user_id])[:40]


async def account_channels(
    connection: AsyncConnection, user_ids: list[str]
) -> dict[str, list[dict[str, Any]]]:
    """批量读取当前页账号的授权渠道；兼容旧成员角色，不遗漏可访问范围。"""
    if not user_ids:
        return {}
    members = TABLES["channel_memberships"]
    channels = channel_metadata.tables["channels"]
    found = (
        await connection.execute(
            select(members, channels.c.name.label("channel_name"))
            .select_from(
                members.outerjoin(
                    channels,
                    and_(
                        channels.c.channel_id == members.c.channel_id,
                        channels.c.id == members.c.channel_id,
                    ),
                )
            )
            .where(
                members.c.user_id.in_(user_ids),
                members.c.channel_id != "system",
                members.c.status == "ACTIVE",
            )
            .order_by(channels.c.name, members.c.channel_id)
        )
    ).mappings()
    result: dict[str, list[dict[str, Any]]] = {}
    for row in found:
        result.setdefault(row["user_id"], []).append(dict(row))
    return result


async def assignment_scopes(
    connection: AsyncConnection, user_id: str, channel_ids: list[str]
) -> list[Scope]:
    if len(channel_ids) != len(set(channel_ids)) or "system" in channel_ids:
        raise ServiceError("VALIDATION_ERROR", "授权渠道重复或无效", 422)
    existing = (await account_channels(connection, [user_id])).get(user_id, [])
    identifiers = set(channel_ids) | {r["channel_id"] for r in existing}
    if len(identifiers) > 400:
        raise ServiceError("VALIDATION_ERROR", "本次渠道授权范围过大", 422)
    # 成员和渠道级授权不使用业务内容范围；每个工作单元仍须显式绑定目标渠道。
    return [Scope(channel_id=c, environment="test") for c in sorted(identifiers)]


def assignment_keys(channel_id: str, user_id: str, event_id: str) -> list[ResourceKey]:
    return [
        policy_key(channel_id),
        record_key(channel_id, "channel_memberships", membership_id(channel_id, user_id)),
        record_key(channel_id, "resource_grants", administrator_grant_id(channel_id, user_id)),
        record_key(channel_id, "audit_events", event_id),
    ]


async def synchronize_channels(
    units: dict[str, UnitOfWork],
    actor: AccountState,
    user_id: str,
    selected: list[str],
    event_id: str,
    request_id: str,
    roles: Sequence[dict[str, Any]],
) -> bool:
    """角色与多渠道授权同事务提交；移除渠道时停用成员，阻断包括既有通配授权的访问。"""
    if not selected and len(units) == 1:
        return False
    require_platform(actor, "channel:govern")
    if len(selected) != len(set(selected)) or "system" in selected:
        raise ServiceError("VALIDATION_ERROR", "授权渠道重复或无效", 422)
    channel_roles = [role for role in roles if role["grant_scope"] == "channel"]
    if selected and (not channel_roles or any(role["state"] != "ACTIVE" for role in channel_roles)):
        raise ServiceError("ROLE_UNAVAILABLE", "请选择启用的渠道角色", 422)
    root = units["system"]
    current = (await account_channels(root.connection, [user_id])).get(user_id, [])
    if not {r["channel_id"] for r in current} <= units.keys():
        raise ServiceError("REVISION_CONFLICT", "渠道授权已变更，请刷新后重试", 409)
    selected_set = set(selected)
    if not selected_set <= units.keys():
        raise ServiceError("SCOPE_MISMATCH", "授权渠道不在本次治理范围内", 403)
    memberships = TABLES["channel_memberships"]
    members = [
        dict(row)
        for row in (
            await root.connection.execute(
                select(memberships).where(
                    memberships.c.channel_id.in_(units.keys()),
                    memberships.c.user_id == user_id,
                )
            )
        ).mappings()
    ]
    previous_members = {row["channel_id"]: row for row in members}
    if len(previous_members) != len(members):
        raise ServiceError("STORAGE_INVARIANT_BROKEN", "渠道成员身份重复", 503)
    channels = channel_metadata.tables["channels"]
    states = {
        row.channel_id: row.status
        for row in (
            await root.connection.execute(
                select(channels.c.channel_id, channels.c.status).where(
                    channels.c.channel_id.in_(selected),
                    channels.c.id == channels.c.channel_id,
                )
            )
        ).all()
    }
    environments = channel_metadata.tables["channel_environments"]
    domains = channel_metadata.tables["data_scopes"]
    pairs_by_channel: dict[str, set[tuple[str, str]]] = {}
    pairs = (
        await root.connection.execute(
            select(environments.c.channel_id, environments.c.environment, domains.c.id)
            .select_from(
                environments.outerjoin(
                    domains,
                    and_(
                        environments.c.channel_id == domains.c.channel_id,
                        environments.c.environment == domains.c.environment,
                        domains.c.status == "ACTIVE",
                    ),
                )
            )
            .where(
                environments.c.channel_id.in_(selected),
                environments.c.status == "ACTIVE",
            )
            .order_by(environments.c.channel_id, environments.c.environment, domains.c.id)
        )
    ).all()
    for pair in pairs:
        channel_pairs = pairs_by_channel.setdefault(pair.channel_id, set())
        # 账号分配覆盖启用环境的管理范围；外部映射缺失不能吞掉渠道管理授权。
        channel_pairs.add(
            (pair.environment, management_scope_id(pair.channel_id, pair.environment))
        )
        if pair.id is not None:
            channel_pairs.add((pair.environment, pair.id))
    grants = TABLES["resource_grants"]
    loaded_grants = {
        row["channel_id"]: dict(row)
        for row in (
            await root.connection.execute(
                select(grants).where(
                    grants.c.channel_id.in_(selected),
                    grants.c.id.in_([administrator_grant_id(c, user_id) for c in selected]),
                    grants.c.grantee_type == "account",
                    grants.c.grantee_id == user_id,
                )
            )
        ).mappings()
    }
    changed = False
    for channel_id, uow in units.items():
        if channel_id == "system":
            continue
        previous = previous_members.get(channel_id)
        uow.read_cache[f"iam:channel_memberships:{membership_id(channel_id, user_id)}"] = previous
        uow.read_cache[f"iam:resource_grants:{administrator_grant_id(channel_id, user_id)}"] = (
            loaded_grants.get(channel_id)
        )
        uow.read_cache[f"iam:audit_events:{event_id}"] = None
        values: dict[str, Any]
        if channel_id not in selected_set:
            if previous is None:
                continue
            values = {"status": "DISABLED"}
            if previous["status"] == "DISABLED":
                continue
        else:
            channel = states.get(channel_id)
            if channel not in {"ACTIVE", "SUSPENDED"}:
                raise ServiceError("CHANNEL_UNAVAILABLE", "授权渠道不存在或已归档", 422)
            channel_pairs = pairs_by_channel.get(channel_id, set())
            # 尚无启用环境时可先登记渠道；管理范围不能签发外部业务身份。
            values = {
                "user_id": user_id,
                "roles": [role["id"] for role in channel_roles],
                "environments": sorted({environment for environment, _ in channel_pairs}),
                "data_scopes": sorted({scope_id for _, scope_id in channel_pairs}),
                "status": "ACTIVE",
                "granted_by": actor.id,
            }
            grant_id = administrator_grant_id(channel_id, user_id)
            grant = loaded_grants.get(channel_id)
            grant_values = {
                "grantee_type": "account",
                "grantee_id": user_id,
                "resource_type": "channel",
                "resource_id": channel_id,
                "environments": values["environments"],
                "data_scopes": values["data_scopes"],
                # 普通授权保存资源范围与上限，不冻结角色动作；角色编辑后实时收窄或扩展。
                "allowed_actions": sorted(ORDINARY_CHANNEL_ACTIONS),
            }
            if not grant or any(grant.get(k) != v for k, v in grant_values.items()):
                await save(
                    uow,
                    "resource_grants",
                    grant_id,
                    grant_values,
                    grant["revision"] if grant else None,
                )
                changed = True
        if previous and all(previous.get(k) == v for k, v in values.items()):
            continue
        await save(
            uow,
            "channel_memberships",
            membership_id(channel_id, user_id),
            values,
            previous["revision"] if previous else None,
        )
        changed = True
        ranges = values if channel_id in selected_set else previous
        assert ranges is not None
        await append_event(
            uow,
            event_id,
            actor.id,
            request_id,
            "membership:put",
            "account",
            user_id,
            ["roles", "environments", "data_scopes", "status"],
            affected_scopes=audit_ranges((ranges["environments"], ranges["data_scopes"])) or None,
        )
    return changed
