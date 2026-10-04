"""渠道存储入口；系统索引只能精确定位，业务记录始终显式限定渠道。"""

import re
from functools import lru_cache
from typing import Any

from sqlalchemy import Select, and_, bindparam, insert, select, update
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine

from creativity_service.core.context import ControlScope, Scope
from creativity_service.core.database import ControlRepository, UnitOfWork, validate_row
from creativity_service.core.database.queries import scoped_select
from creativity_service.core.locking import ResourceKey, record_key
from creativity_service.core.primitives import ServiceError, digest, utcnow
from creativity_service.modules.channels.tables import metadata
from creativity_service.modules.iam.repositories import policy_key

SYSTEM_TABLES = {"key_identity_index", "channel_code_index"}


def environment_id(channel_id: str, environment: str) -> str:
    return "env_" + digest([channel_id, environment])[:40]


def mapping_key(channel_id: str, environment: str, kind: str, external_id: str) -> ResourceKey:
    return ResourceKey(channel_id, "data-scope-mapping", (environment, kind, external_id))


async def rows(
    connection: AsyncConnection, name: str, channel_id: str, **filters: Any
) -> list[dict[str, Any]]:
    if not channel_id or (name in SYSTEM_TABLES and channel_id != "system"):
        raise ServiceError("CONTEXT_REQUIRED", "缺少有效渠道范围", 403)
    if name == "key_identity_index":
        raise ServiceError("CONTROL_TABLE", "密钥身份索引只允许按完整摘要精确查找", 403)
    table = metadata.tables[name]
    statement, parameters = scoped_select(table, {"channel_id": channel_id}, filters)
    result = await connection.execute(statement, parameters)
    return [dict(row) for row in result.mappings()]


@lru_cache(maxsize=1)
def _scope_statement() -> Select[Any]:
    channel, environment, domain = (
        metadata.tables[name] for name in ("channels", "channel_environments", "data_scopes")
    )
    joined = channel.outerjoin(
        environment,
        and_(
            environment.c.channel_id == channel.c.channel_id,
            environment.c.environment == bindparam("environment"),
        ),
    ).outerjoin(
        domain,
        and_(
            domain.c.channel_id == channel.c.channel_id,
            domain.c.environment == bindparam("environment"),
            domain.c.id == bindparam("data_scope_id"),
        ),
    )
    return (
        select(channel, environment, domain)
        .select_from(joined)
        .where(
            channel.c.channel_id == bindparam("channel_id"), channel.c.id == bindparam("channel_id")
        )
    )


async def scope_rows(connection: AsyncConnection, scope: Scope) -> dict[str, dict[str, Any]]:
    """关联读取渠道、环境和域；保留缺失及重复检查，状态由调用服务判断。"""
    found = (
        (
            await connection.execute(
                _scope_statement(),
                {
                    "channel_id": scope.channel_id,
                    "environment": scope.environment,
                    "data_scope_id": scope.data_scope_id,
                },
            )
        )
        .mappings()
        .all()
    )
    if len(found) > 1:
        raise ServiceError("STORAGE_INVARIANT_BROKEN", "渠道范围记录重复，请联系管理员", 503)
    result = {
        name: {column.name: found[0][column] for column in table.c}
        for name in ("channels", "channel_environments", "data_scopes")
        for table in [metadata.tables[name]]
        if found and found[0][table.c.id] is not None
    }
    required_names = {"channels", "channel_environments"}
    if scope.data_scope_id:
        required_names.add("data_scopes")
    if not required_names <= result.keys():
        raise ServiceError("NOT_FOUND", "请求资源不存在", 404)
    return result


async def one(
    connection: AsyncConnection, name: str, channel_id: str, **filters: Any
) -> dict[str, Any] | None:
    values = await rows(connection, name, channel_id, **filters)
    if len(values) > 1:
        raise ServiceError("STORAGE_INVARIANT_BROKEN", "渠道记录重复，请联系管理员", 503)
    return values[0] if values else None


async def required(
    connection: AsyncConnection, name: str, channel_id: str, **filters: Any
) -> dict[str, Any]:
    value = await one(connection, name, channel_id, **filters)
    if value is None:
        raise ServiceError("NOT_FOUND", "请求资源不存在", 404)
    return value


async def save(
    uow: UnitOfWork,
    name: str,
    record_id: str,
    values: dict[str, Any],
    revision: int | None = None,
) -> dict[str, Any]:
    channel_id = uow.scope.channel_id
    if name in SYSTEM_TABLES:
        raise ServiceError("CONTROL_TABLE", "身份索引须通过受限入口写入", 403)
    uow.require_lock(policy_key(channel_id))
    uow.require_lock(record_key(channel_id, name, record_id))
    table = metadata.tables[name]
    current = await one(uow.connection, name, channel_id, id=record_id)
    if (current is None and revision is not None) or (
        current is not None and current["revision"] != revision
    ):
        raise ServiceError("REVISION_CONFLICT", "数据已变更，请刷新后重试", 409)
    protected = {"id", "channel_id", "revision", "created_at", "updated_at"}
    if protected & values.keys():
        raise ServiceError("CONTEXT_OVERRIDE", "不能覆盖渠道归属", 422)
    immutable = {
        "channels": {"channel_code", "business_type"},
        "channel_environments": {"environment"},
        "data_scopes": {"environment", "external_scope_type", "external_scope_id"},
        "service_clients": {"environment"},
        "channel_keys": {"environment", "client_id", "secret_digest", "prefix", "suffix"},
    }.get(name, set())
    if current and any(k in values and current[k] != values[k] for k in immutable):
        raise ServiceError("IMMUTABLE_FIELD", "资源归属创建后不能改绑", 422)
    if name == "channels" and record_id != channel_id:
        raise ServiceError("SCOPE_MISMATCH", "渠道主档须归自身", 403)
    now = utcnow()
    row = {
        **(current or {}),
        **values,
        "id": record_id,
        "channel_id": channel_id,
        "created_at": current["created_at"] if current else now,
        "updated_at": now,
        "revision": current["revision"] + 1 if current else 1,
    }
    validate_row(table, row)
    if current:
        await uow.connection.execute(
            update(table)
            .where(table.c.channel_id == channel_id, table.c.id == record_id)
            .values(**row)
        )
    else:
        await uow.connection.execute(insert(table).values(**row))
    return row


class ChannelRepository:
    def __init__(self, engine: AsyncEngine) -> None:
        self.engine = engine

    async def directory_records(
        self, connection: AsyncConnection, limit: int
    ) -> list[dict[str, Any]]:
        """平台治理入口先限定目录，再关联精确目标渠道，不把系统渠道当业务通配。"""
        index = metadata.tables["channel_code_index"]
        channel = metadata.tables["channels"]
        directory = (
            select(index.c.target_channel_id)
            .where(index.c.channel_id == "system", index.c.target_channel_id != "system")
            .order_by(index.c.created_at, index.c.id)
            .limit(limit)
            .subquery()
        )
        records = [
            dict(row)
            for row in (
                await connection.execute(
                    select(channel).select_from(
                        directory.join(
                            channel,
                            (channel.c.channel_id == directory.c.target_channel_id)
                            & (channel.c.id == directory.c.target_channel_id),
                        )
                    )
                )
            ).mappings()
        ]
        if len({row["id"] for row in records}) != len(records):
            raise ServiceError("STORAGE_INVARIANT_BROKEN", "渠道记录重复，请联系管理员", 503)
        return records

    async def record_use(self, uow: UnitOfWork, key_id: str) -> None:
        """使用时间是观测元数据，不递增配置修订，避免繁忙 Key 的管理操作饥饿。"""
        channel_id = uow.scope.channel_id
        uow.require_lock(policy_key(channel_id))
        uow.require_lock(record_key(channel_id, "channel_keys", key_id))
        row = await required(uow.connection, "channel_keys", channel_id, id=key_id)
        row["last_used_at"] = utcnow()
        table = metadata.tables["channel_keys"]
        validate_row(table, row)
        await uow.connection.execute(
            update(table)
            .where(table.c.channel_id == channel_id, table.c.id == key_id)
            .values(last_used_at=row["last_used_at"])
        )

    async def directory(self, connection: AsyncConnection) -> list[str]:
        # 只枚举系统渠道中的定位元数据；调用方负责平台治理或成员授权过滤。
        return [
            r["target_channel_id"]
            for r in await rows(connection, "channel_code_index", "system")
            if r["target_channel_id"] != "system"
        ]

    async def locate_key(
        self, connection: AsyncConnection, key_digest: str
    ) -> dict[str, Any] | None:
        if not re.fullmatch(r"[0-9a-f]{64}", key_digest):
            raise ServiceError("UNAUTHENTICATED", "接入凭据无效", 401)
        return await ControlRepository(
            metadata.tables["key_identity_index"],
            ControlScope(purpose="identity_lookup", actor_id="key_authentication"),
        ).lookup(connection, "key_lookup_digest", key_digest)

    async def add_index(
        self, uow: UnitOfWork, name: str, record_id: str, values: dict[str, str]
    ) -> None:
        if name not in SYSTEM_TABLES or values.get("target_channel_id") != uow.scope.channel_id:
            raise ServiceError("CONTROL_SCOPE_INVALID", "索引与已核准渠道不符", 403)
        uow.require_lock(policy_key("system"))
        uow.require_lock(record_key("system", name, record_id))
        column = "key_lookup_digest" if name == "key_identity_index" else "channel_code"
        uow.require_lock(ResourceKey("system", name, (values[column],)))
        existing = await ControlRepository(
            metadata.tables[name],
            ControlScope(
                purpose="identity_lookup" if name == "key_identity_index" else "channel_directory",
                actor_id="channel_index_writer",
            ),
        ).lookup(uow.connection, column, values[column])
        if existing:
            raise ServiceError("CODE_EXISTS", "编码或凭据已存在", 409)
        table = metadata.tables[name]
        now = utcnow()
        row = {
            **values,
            "id": record_id,
            "channel_id": "system",
            "created_at": now,
            "updated_at": now,
            "revision": 1,
        }
        validate_row(table, row)
        await uow.connection.execute(insert(table).values(**row))
