"""显式范围仓储与可由多个服务共用的短事务工作单元。"""

from collections.abc import AsyncIterator, Mapping
from contextlib import asynccontextmanager
from contextvars import ContextVar
from datetime import datetime
from typing import Any

from sqlalchemy import Table, and_, delete, insert, select, update
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine
from sqlalchemy.sql.elements import ColumnElement

from creativity_service.core.context import ControlScope, Scope
from creativity_service.core.locking import ResourceKey, acquire_locks, record_key
from creativity_service.core.primitives import ServiceError, canonical_json, utcnow

transaction_active: ContextVar[bool] = ContextVar("transaction_active", default=False)


def assert_external_io_allowed() -> None:
    if transaction_active.get():
        raise RuntimeError("短事务中不能等待外部调用")


class UnitOfWork:
    def __init__(
        self, connection: AsyncConnection, scope: Scope | ControlScope, keys: frozenset[ResourceKey]
    ) -> None:
        self.connection, self.scope, self.keys = connection, scope, keys
        self.active = True

    def require_lock(self, key: ResourceKey) -> None:
        if not self.active or key not in self.keys or not self.connection.in_transaction():
            raise RuntimeError("写入前须在同一活动事务中取得约定互斥锁")

    def require_scope(self, scope: Scope | ControlScope) -> None:
        if not self.active or scope != self.scope:
            raise ServiceError("SCOPE_MISMATCH", "事务范围与服务上下文不符", 403)


@asynccontextmanager
async def transaction(
    engine: AsyncEngine, scope: Scope | ControlScope, keys: list[ResourceKey]
) -> AsyncIterator[UnitOfWork]:
    if not isinstance(scope, (Scope, ControlScope)):
        raise ServiceError("CONTEXT_REQUIRED", "缺少受信范围", 403)
    if transaction_active.get():
        raise RuntimeError("不能嵌套独立事务；请传入调用方的工作单元")
    if not keys:
        raise ValueError("事务必须预先声明全部互斥资源")
    if any(key.channel_id not in {scope.channel_id, "system"} for key in keys):
        raise ServiceError("LOCK_SCOPE_MISMATCH", "锁渠道与上下文不符", 403)
    token = transaction_active.set(True)
    uow = None
    try:
        async with engine.begin() as connection:
            if connection.dialect.name != "postgresql":
                raise RuntimeError("事务互斥只支持 PostgreSQL")
            if await connection.get_isolation_level() != "READ COMMITTED":
                raise RuntimeError("事务互斥协议要求 READ COMMITTED 隔离级别")
            await acquire_locks(connection, frozenset(keys))
            uow = UnitOfWork(connection, scope, frozenset(keys))
            yield uow
    finally:
        if uow is not None:
            uow.active = False
        transaction_active.reset(token)


def scope_values(table: Table, scope: Scope | ControlScope) -> dict[str, Any]:
    return {key: value for key, value in scope.model_dump().items() if key in table.c}


def validate_row(table: Table, values: Mapping[str, Any]) -> None:
    if set(values) != set(table.c.keys()):
        raise ValueError("写入必须显式提供全部模型字段，不能依赖数据库默认值")
    for column in table.c:
        value = values[column.name]
        if value is None:
            if column.info.get("required"):
                raise ValueError(f"{column.comment}不能为空")
            continue
        python_type = column.type.python_type
        if python_type is int and (not isinstance(value, int) or isinstance(value, bool)):
            raise ValueError(f"{column.comment}必须为整数")
        if python_type not in (dict, int) and not isinstance(value, python_type):
            raise ValueError(f"{column.comment}类型不正确")
        if python_type is dict:
            canonical_json(value)
        if column.info.get("required") and isinstance(value, str) and not value:
            raise ValueError(f"{column.comment}不能为空")
        length = getattr(column.type, "length", None)
        if length and isinstance(value, str) and len(value) > length:
            raise ValueError(f"{column.comment}长度超限")
        if isinstance(value, datetime) and value.tzinfo is None:
            raise ValueError("时间必须包含时区")


class Repository:
    def __init__(self, table: Table, scope: Scope) -> None:
        if not isinstance(scope, Scope):
            raise ServiceError("CONTEXT_REQUIRED", "业务仓储必须显式接收业务范围", 403)
        if not {"id", "channel_id"} <= set(table.c.keys()):
            raise ServiceError("STORAGE_SCOPE_MISSING", "仓储表缺少标识或渠道字段", 503)
        if table.info.get("control_purpose") not in (None, "audit"):
            raise ServiceError("CONTROL_TABLE", "业务仓储不能访问平台控制表", 403)
        self.table, self.scope = table, scope

    def predicate(self) -> ColumnElement[bool]:
        return and_(
            *(
                self.table.c[key] == value
                for key, value in scope_values(self.table, self.scope).items()
            )
        )

    async def get(self, connection: AsyncConnection, record_id: str) -> dict[str, Any] | None:
        rows = (
            (
                await connection.execute(
                    select(self.table).where(self.predicate(), self.table.c.id == record_id)
                )
            )
            .mappings()
            .all()
        )
        if len(rows) > 1:
            raise ServiceError("STORAGE_INVARIANT_BROKEN", "记录标识重复，请联系管理员", 503)
        return dict(rows[0]) if rows else None

    async def find(self, connection: AsyncConnection, **filters: Any) -> list[dict[str, Any]]:
        if set(filters) - set(self.table.c.keys()):
            raise ValueError("筛选字段不存在")
        statement = select(self.table).where(
            self.predicate(), *(self.table.c[key] == value for key, value in filters.items())
        )
        return [dict(row) for row in (await connection.execute(statement)).mappings()]

    def _validate(self, values: Mapping[str, Any]) -> None:
        validate_row(self.table, values)

    async def add(
        self, uow: UnitOfWork, record_id: str, values: Mapping[str, Any]
    ) -> dict[str, Any]:
        uow.require_scope(self.scope)
        uow.require_lock(record_key(self.scope.channel_id, self.table.name, record_id))
        protected = {
            "id",
            "channel_id",
            "environment",
            "data_scope_id",
            "subject_type",
            "subject_id",
            "created_at",
            "updated_at",
            "revision",
        }
        if set(values) & protected:
            raise ServiceError("CONTEXT_OVERRIDE", "不能通过正文覆盖归属或服务元数据", 422)
        existing = await uow.connection.scalar(
            select(self.table.c.id)
            .where(self.table.c.channel_id == self.scope.channel_id, self.table.c.id == record_id)
            .limit(1)
        )
        if existing is not None:
            raise ServiceError("DUPLICATE_ID", "记录标识已存在")
        now = utcnow()
        row = {
            **values,
            **scope_values(self.table, self.scope),
            "id": record_id,
            "created_at": now,
            "updated_at": now,
            "revision": 1,
        }
        # 可空的范围字段也由服务上下文明确写入。
        for name in ("environment", "data_scope_id", "subject_type", "subject_id"):
            if name in self.table.c and name not in row:
                row[name] = None
        self._validate(row)
        await uow.connection.execute(insert(self.table).values(**row))
        return row

    async def change(
        self, uow: UnitOfWork, record_id: str, revision: int, values: Mapping[str, Any]
    ) -> dict[str, Any]:
        uow.require_scope(self.scope)
        uow.require_lock(record_key(self.scope.channel_id, self.table.name, record_id))
        row = await self.get(uow.connection, record_id)
        if row is None:
            raise ServiceError("NOT_FOUND", "记录不存在", 404)
        if row["revision"] != revision:
            raise ServiceError("REVISION_CONFLICT", "数据已变更，请刷新后重试")
        mutable = set(self.table.c.keys()) - {
            "id",
            "channel_id",
            "environment",
            "data_scope_id",
            "subject_type",
            "subject_id",
            "created_at",
            "updated_at",
            "revision",
        }
        if set(values) - mutable:
            raise ServiceError("IMMUTABLE_FIELD", "不能修改归属或服务元数据", 422)
        changed = {**row, **values, "updated_at": utcnow(), "revision": revision + 1}
        self._validate(changed)
        await uow.connection.execute(
            update(self.table)
            .where(self.predicate(), self.table.c.id == record_id)
            .values(**changed)
        )
        return changed

    async def remove(self, uow: UnitOfWork, record_id: str) -> None:
        uow.require_scope(self.scope)
        uow.require_lock(record_key(self.scope.channel_id, self.table.name, record_id))
        await uow.connection.execute(
            delete(self.table).where(self.predicate(), self.table.c.id == record_id)
        )


class ControlRepository:
    """仅支持登记用途下的精确查找，不能将系统渠道当作业务查询通配。"""

    def __init__(self, table: Table, scope: ControlScope) -> None:
        if (
            not isinstance(scope, ControlScope)
            or table.info.get("control_purpose") != scope.purpose
        ):
            raise ServiceError("CONTROL_SCOPE_INVALID", "控制面用途不匹配", 403)
        self.table, self.scope = table, scope

    async def lookup(
        self, connection: AsyncConnection, column: str, value: str
    ) -> dict[str, Any] | None:
        if column not in self.table.c or column == "channel_id" or not value:
            raise ValueError("控制面查找须指定精确身份或对象键")
        rows = (
            (
                await connection.execute(
                    select(self.table).where(
                        self.table.c.channel_id == "system", self.table.c[column] == value
                    )
                )
            )
            .mappings()
            .all()
        )
        if len(rows) > 1:
            raise ServiceError("STORAGE_INVARIANT_BROKEN", "控制记录重复", 503)
        return dict(rows[0]) if rows else None
