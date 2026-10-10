"""IAM 受控存储，所有写入使用公共事务锁并显式填充字段。"""

from contextlib import AbstractAsyncContextManager
from typing import Any

from sqlalchemy import Table, and_, insert, or_, select, update
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine

from creativity_service.core.auth.types import AccountState, GrantState, MembershipState, Revocation
from creativity_service.core.context import ControlScope, Scope
from creativity_service.core.database import UnitOfWork, validate_row
from creativity_service.core.database.queries import scoped_select
from creativity_service.core.database.reading import read_connection
from creativity_service.core.database.soft_delete import active_rows
from creativity_service.core.database.tables import metadata as core_metadata
from creativity_service.core.locking import ResourceKey, record_key
from creativity_service.core.primitives import Contract, ServiceError, digest, utcnow
from creativity_service.modules.iam.management_tables import metadata as management_metadata
from creativity_service.modules.iam.operations_tables import metadata as operations_metadata
from creativity_service.modules.iam.tables import metadata

TABLES = {
    **core_metadata.tables,
    **metadata.tables,
    **operations_metadata.tables,
    **management_metadata.tables,
}


def policy_key(channel_id: str) -> ResourceKey:
    return ResourceKey(channel_id, "iam-policy", ("all",))


def membership_id(channel_id: str, user_id: str) -> str:
    return "member_" + digest([channel_id, user_id])[:40]


def to_state[T: Contract](model: type[T], row: dict[str, Any]) -> T:
    return model.model_validate({k: v for k, v in row.items() if k in model.model_fields})


async def rows(
    connection: AsyncConnection,
    name: str,
    channel_id: str,
    *,
    include_deleted: bool = False,
    **filters: Any,
) -> list[dict[str, Any]]:
    if not channel_id:
        raise ValueError("必须指定渠道")
    table = TABLES[name]
    statement, parameters = scoped_select(
        table, {"channel_id": channel_id}, filters, include_deleted=include_deleted
    )
    result = await connection.execute(statement, parameters)
    return [dict(row) for row in result.mappings()]


async def one(
    connection: AsyncConnection,
    name: str,
    channel_id: str,
    *,
    include_deleted: bool = False,
    **filters: Any,
) -> dict[str, Any] | None:
    result = await rows(connection, name, channel_id, include_deleted=include_deleted, **filters)
    if len(result) > 1:
        raise ServiceError("STORAGE_INVARIANT_BROKEN", "身份记录重复，请联系管理员", 503)
    return result[0] if result else None


async def save(
    uow: UnitOfWork,
    name: str,
    record_id: str,
    values: dict[str, Any],
    expected_revision: int | None = None,
    *,
    restore_deleted: bool = False,
) -> dict[str, Any]:
    channel_id = uow.scope.channel_id
    uow.require_lock(record_key(channel_id, name, record_id))
    table: Table = TABLES[name]
    if name != "audit_events":
        uow.require_lock(policy_key(channel_id))
    if name in {"platform_accounts", "builtin_roles"} and channel_id != "system":
        raise ServiceError("SCOPE_MISMATCH", "平台身份必须归系统渠道", 403)
    protected = {"id", "channel_id", "created_at", "updated_at", "revision"}
    if protected & values.keys():
        raise ServiceError("CONTEXT_OVERRIDE", "不能覆盖身份归属", 422)
    cache_key = f"iam:{name}:{record_id}"
    current = (
        uow.read_cache[cache_key]
        if cache_key in uow.read_cache
        else await one(uow.connection, name, channel_id, id=record_id, include_deleted=True)
    )
    if restore_deleted and (
        name not in {"channel_memberships", "resource_grants"}
        or values.get("is_deleted") is not False
    ):
        raise ServiceError("RESTORE_INVALID", "只能通过重新授权恢复成员或资源授权", 422)
    if current and current["is_deleted"] and not restore_deleted:
        raise ServiceError("NOT_FOUND", "记录已删除", 404)
    if (current is None and expected_revision is not None) or (
        current is not None and current["revision"] != expected_revision
    ):
        raise ServiceError("REVISION_CONFLICT", "数据已变更，请刷新后重试", 409)
    now = utcnow()
    value = {
        "is_deleted": False,
        **(current or {}),
        **values,
        "id": record_id,
        "channel_id": channel_id,
        "created_at": current["created_at"] if current else now,
        "updated_at": now,
        "revision": current["revision"] + 1 if current else 1,
    }
    # 控制面也复用公共字段校验，但不伪造业务 Scope 或跨渠道查询。
    validate_row(table, value)
    if current:
        await uow.connection.execute(
            update(table)
            .where(table.c.channel_id == channel_id, table.c.id == record_id)
            .values(**value)
        )
    else:
        await uow.connection.execute(insert(table).values(**value))
    if cache_key in uow.read_cache:
        uow.read_cache[cache_key] = value
    return value


async def role_catalog(
    connection: AsyncConnection, channel_id: str, *, all_scopes: bool = False
) -> dict[str, dict[str, Any]]:
    """角色管理、账号选择与鉴权共用数据库目录；系统定义不直接赋予业务访问权。"""
    custom = TABLES["custom_roles"]
    found = (
        await connection.execute(
            active_rows(select(custom).where(custom.c.channel_id.in_({"system", channel_id})))
        )
    ).mappings()
    return role_catalog_rows(
        [dict(row) for row in found],
        await rows(connection, "builtin_roles", "system"),
        platform=channel_id == "system",
        all_scopes=all_scopes,
    )


def role_catalog_rows(
    custom: list[dict[str, Any]],
    builtin: list[dict[str, Any]],
    *,
    platform: bool = False,
    all_scopes: bool = False,
) -> dict[str, dict[str, Any]]:
    scope = "platform" if platform else "channel"
    if len(builtin) != len({row["role_code"] for row in builtin}):
        raise ServiceError("STORAGE_INVARIANT_BROKEN", "内置角色编码重复", 503)
    result = {
        row["role_code"]: {
            **row,
            "id": row["role_code"],
            "state": "ACTIVE",
            "builtin": True,
            "menu_ids": row.get("menu_ids"),
        }
        for row in builtin
        if all_scopes or row["grant_scope"] == scope
    }
    for row in custom:
        if all_scopes or row["grant_scope"] == scope:
            if row["id"] in result:
                raise ServiceError("STORAGE_INVARIANT_BROKEN", "角色标识重复", 503)
            result[row["id"]] = {
                **row,
                "builtin": False,
                "account_assignable": row["channel_id"] == "system",
            }
    return dict(
        sorted(
            result.items(),
            key=lambda item: (
                item[1]["grant_scope"] != "platform",
                not item[1]["builtin"],
                item[1]["name"],
                item[0],
            ),
        )
    )


async def resolved_actions(
    connection: AsyncConnection, channel_id: str, codes: list[str], *, require_active: bool = False
) -> frozenset[str]:
    catalog = await role_catalog(connection, channel_id)
    if require_active and any(
        code not in catalog or catalog[code]["state"] != "ACTIVE" for code in codes
    ):
        raise ServiceError("ROLE_UNAVAILABLE", "角色不存在、已停用或不属于当前渠道", 403)
    return frozenset(
        a
        for code in codes
        if code in catalog and catalog[code]["state"] == "ACTIVE"
        for a in catalog[code]["allowed_actions"]
    )


async def membership_state(
    connection: AsyncConnection, row: dict[str, Any], override: dict[str, Any] | None = None
) -> MembershipState:
    catalog = await role_catalog(connection, row["channel_id"])
    if override:
        catalog[override["id"]] = override
    return membership_from_catalog(row, catalog)


def membership_from_catalog(
    row: dict[str, Any], catalog: dict[str, dict[str, Any]]
) -> MembershipState:
    codes = [
        code
        for code in row["roles"]
        if code in catalog
        and catalog[code]["state"] == "ACTIVE"
        and catalog[code]["grant_scope"] == "channel"
    ]
    return to_state(
        MembershipState,
        {
            **row,
            "roles": codes,
            "custom_actions": frozenset(
                a for code in codes for a in catalog[code]["allowed_actions"]
            ),
        },
    )


def account_from_catalog(row: dict[str, Any], catalog: dict[str, dict[str, Any]]) -> AccountState:
    codes = [
        code
        for code in row["platform_roles"]
        if code in catalog
        and catalog[code]["state"] == "ACTIVE"
        and catalog[code]["grant_scope"] == "platform"
    ]
    return to_state(
        AccountState,
        {
            **row,
            "platform_roles": codes,
            "custom_actions": frozenset(
                a for code in codes for a in catalog[code]["allowed_actions"]
            ),
        },
    )


async def account_state(connection: AsyncConnection, row: dict[str, Any]) -> AccountState:
    return account_from_catalog(row, await role_catalog(connection, "system"))


class IdentityRepository:
    def __init__(self, engine: AsyncEngine) -> None:
        self.engine = engine

    def read_scope(self) -> AbstractAsyncContextManager[AsyncConnection]:
        return read_connection(self.engine)

    async def account(self, user_id: str) -> AccountState | None:
        async with read_connection(self.engine) as connection:
            row = await one(connection, "platform_accounts", "system", id=user_id)
            return await account_state(connection, row) if row else None

    async def accounts(self, user_ids: list[str]) -> dict[str, AccountState]:
        table = TABLES["platform_accounts"]
        identifiers = list(dict.fromkeys(user_ids))
        result: dict[str, AccountState] = {}
        async with self.engine.connect() as connection:
            catalog = await role_catalog(connection, "system")
            for start in range(0, len(identifiers), 500):
                found = (
                    await connection.execute(
                        active_rows(
                            select(table).where(
                                table.c.channel_id == "system",
                                table.c.id.in_(identifiers[start : start + 500]),
                            )
                        )
                    )
                ).mappings()
                for row in found:
                    if row["id"] in result:
                        raise ServiceError("STORAGE_INVARIANT_BROKEN", "账号标识重复", 503)
                    result[row["id"]] = account_from_catalog(dict(row), catalog)
        return result

    async def credentials(self, login_name: str) -> dict[str, Any] | None:
        async with self.engine.connect() as connection:
            return await one(connection, "platform_accounts", "system", login_name=login_name)

    async def membership(self, channel_id: str, user_id: str) -> MembershipState | None:
        async with read_connection(self.engine) as connection:
            row = await one(connection, "channel_memberships", channel_id, user_id=user_id)
            return await membership_state(connection, row) if row else None

    async def grants(self, channel_id: str) -> list[GrantState]:
        async with read_connection(self.engine) as connection:
            result = await rows(connection, "resource_grants", channel_id)
        return [to_state(GrantState, row) for row in result]

    async def token_revoked(self, channel_id: str, token_digest: str) -> bool:
        async with read_connection(self.engine) as connection:
            return bool(
                await rows(
                    connection, "iam_revocations", channel_id, kind="token", target_id=token_digest
                )
            )

    async def pending_revocations(self, limit: int = 100) -> list[Revocation]:
        # 补偿扫描只返回待撤销身份索引，不可作为业务数据查询入口。
        table = TABLES["iam_revocations"]
        async with self.engine.connect() as connection:
            result = await connection.execute(
                active_rows(
                    select(table)
                    .where(table.c.completed_at.is_(None))
                    .order_by(table.c.created_at, table.c.id)
                    .limit(limit)
                )
            )
            return [to_state(Revocation, dict(row)) for row in result.mappings()]

    async def audit_rows(self, scope: Scope | ControlScope, limit: int) -> list[dict[str, Any]]:
        table = TABLES["audit_events"]
        predicate = table.c.channel_id == scope.channel_id
        if isinstance(scope, Scope):
            predicate = and_(
                predicate,
                or_(
                    table.c.summary["affected_scopes"].contains(
                        [{"environment": scope.environment}]
                    ),
                    and_(
                        ~table.c.summary.has_key("affected_scopes"),
                        table.c.environment == scope.environment,
                    ),
                ),
            )
        async with self.engine.connect() as connection:
            result = await connection.execute(
                active_rows(
                    select(table)
                    .where(predicate)
                    .order_by(table.c.created_at.desc(), table.c.id.desc())
                    .limit(limit)
                )
            )
            return [dict(row) for row in result.mappings()]
