"""显式范围仓储与可由多个服务共用的短事务工作单元。"""

from collections.abc import AsyncIterator, Iterable, Mapping
from contextlib import asynccontextmanager
from contextvars import ContextVar
from datetime import datetime
from typing import Any

from sqlalchemy import Table, and_, delete, insert, select, update
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine
from sqlalchemy.sql.elements import ColumnElement

from creativity_service.core.context import ControlScope, Scope
from creativity_service.core.database.inserts import InsertBatch
from creativity_service.core.database.queries import scoped_select
from creativity_service.core.database.soft_delete import active_rows, soft_delete
from creativity_service.core.locking import (
    ResourceKey,
    acquire_locks,
    lock_order,
    normalize_keys,
    read_key,
    record_key,
)
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
        self.read_cache: dict[str, Any] = {}

    def require_lock(self, key: ResourceKey) -> None:
        if not self.active or key not in self.keys or not self.connection.in_transaction():
            raise RuntimeError("写入前须在同一活动事务中取得约定互斥锁")

    def require_read_lock(self, key: ResourceKey) -> None:
        """排他锁也满足读取要求，共享锁不能满足写入要求。"""
        exclusive = ResourceKey(key.channel_id, key.resource_type, key.business_key)
        self.require_lock(exclusive if exclusive in self.keys else read_key(key))

    async def acquire(self, keys: list[ResourceKey]) -> None:
        """只能按统一顺序追加末尾的锁，不能升级已经持有的共享锁。"""
        if not self.active or not self.connection.in_transaction():
            raise RuntimeError("追加互斥锁必须位于活动事务内")
        requested = normalize_keys(frozenset(keys))
        if any(key.channel_id not in {self.scope.channel_id, "system"} for key in requested):
            raise ServiceError("LOCK_SCOPE_MISMATCH", "锁渠道与上下文不符", 403)
        held = {(key.channel_id, key.resource_type, key.business_key): key for key in self.keys}
        pending = set()
        for key in requested:
            previous = held.get((key.channel_id, key.resource_type, key.business_key))
            if previous is not None:
                if previous.shared and not key.shared:
                    raise RuntimeError("不能在事务中升级共享锁，请预先声明写入资源")
            else:
                if not key.shared and any(
                    previous.shared and previous.lock_id == key.lock_id for previous in self.keys
                ):
                    raise RuntimeError("不能在事务中升级共享锁，请预先声明写入资源")
                pending.add(key)
        if (
            pending
            and self.keys
            and min(map(lock_order, pending)) <= max(map(lock_order, self.keys))
        ):
            raise RuntimeError("追加锁必须遵守全局资源顺序")
        await acquire_locks(self.connection, frozenset(pending))
        self.keys |= frozenset(pending)

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
    from creativity_service.core.deletion.ledger import DeletionLedger, current_manifest

    manifest = None
    if isinstance(scope, Scope) and any(key.resource_type == "content-graph" for key in keys):
        ledger = DeletionLedger()
        manifest = await ledger.operate(scope.channel_id)
        if "updated_at" not in manifest:
            from creativity_service.core.database.tables import metadata

            # 只有渠道首次建立空范围时可初始化独立清单；既有屏障不能掩盖清单丢失。
            barriers = metadata.tables["recovery_barriers"]
            async with engine.connect() as connection:
                existing = await connection.scalar(
                    active_rows(
                        select(barriers.c.id)
                        .where(barriers.c.channel_id == scope.channel_id)
                        .limit(1)
                    )
                )
            if existing is not None:
                raise ServiceError("DELETION_LEDGER_UNAVAILABLE", "无法确认最新删除清单", 503)
            manifest = await ledger.operate(scope.channel_id, initialize=True)
    manifest_token = current_manifest.set(manifest)
    token = transaction_active.set(True)
    uow = None
    try:
        async with engine.begin() as connection:
            if connection.dialect.name != "mysql":
                raise RuntimeError("事务互斥只支持 MySQL 8")
            locked = normalize_keys(frozenset(keys))
            await acquire_locks(connection, locked)
            uow = UnitOfWork(connection, scope, locked)
            yield uow
    finally:
        if uow is not None:
            uow.active = False
        transaction_active.reset(token)
        current_manifest.reset(manifest_token)


@asynccontextmanager
async def control_transaction(
    engine: AsyncEngine,
    scope: ControlScope,
    scopes: list[Scope],
    keys: list[ResourceKey],
) -> AsyncIterator[dict[str, UnitOfWork]]:
    """平台账号授权专用：明确列出目标渠道，共享一次提交但各自保持独立工作单元。"""
    if not isinstance(scope, ControlScope) or scope.purpose != "accounts":
        raise ServiceError("CONTEXT_REQUIRED", "缺少账号治理范围", 403)
    if transaction_active.get():
        raise RuntimeError("不能嵌套独立事务；请传入调用方的工作单元")
    located: dict[str, Scope | ControlScope] = {scope.channel_id: scope}
    for target in scopes:
        if target.channel_id == "system" or target.channel_id in located:
            raise ServiceError("SCOPE_MISMATCH", "账号授权渠道范围重复或无效", 403)
        located[target.channel_id] = target
    if not keys or any(key.channel_id not in located for key in keys):
        raise ServiceError("LOCK_SCOPE_MISMATCH", "锁渠道与显式治理范围不符", 403)
    if any(key.resource_type == "content-graph" for key in keys):
        raise ServiceError("SCOPE_MISMATCH", "账号治理事务不能执行业务内容操作", 403)
    token = transaction_active.set(True)
    units: dict[str, UnitOfWork] = {}
    try:
        async with engine.begin() as connection:
            if connection.dialect.name != "mysql":
                raise RuntimeError("事务互斥只支持 MySQL 8")
            locked = normalize_keys(frozenset(keys))
            await acquire_locks(connection, locked)
            units = {
                channel_id: UnitOfWork(
                    connection,
                    target,
                    frozenset(k for k in locked if k.channel_id in {channel_id, "system"}),
                )
                for channel_id, target in located.items()
            }
            yield units
    finally:
        for unit in units.values():
            unit.active = False
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
    def __init__(self, table: Table, scope: Scope, *, include_deleted: bool = False) -> None:
        if not isinstance(scope, Scope):
            raise ServiceError("CONTEXT_REQUIRED", "业务仓储必须显式接收业务范围", 403)
        if not {"id", "channel_id"} <= set(table.c.keys()):
            raise ServiceError("STORAGE_SCOPE_MISSING", "仓储表缺少标识或渠道字段", 503)
        if table.info.get("control_purpose") not in (None, "audit"):
            raise ServiceError("CONTROL_TABLE", "业务仓储不能访问平台控制表", 403)
        self.table, self.scope = table, scope
        self.include_deleted = include_deleted

    def predicate(self) -> ColumnElement[bool]:
        return and_(
            *(
                self.table.c[key] == value
                for key, value in scope_values(self.table, self.scope).items()
            )
        )

    async def get(self, connection: AsyncConnection, record_id: str) -> dict[str, Any] | None:
        statement, parameters = scoped_select(
            self.table,
            scope_values(self.table, self.scope),
            {"id": record_id},
            include_deleted=self.include_deleted,
        )
        rows = (await connection.execute(statement, parameters)).mappings().all()
        if len(rows) > 1:
            raise ServiceError("STORAGE_INVARIANT_BROKEN", "记录标识重复，请联系管理员", 503)
        return dict(rows[0]) if rows else None

    async def find(self, connection: AsyncConnection, **filters: Any) -> list[dict[str, Any]]:
        if set(filters) - set(self.table.c.keys()):
            raise ValueError("筛选字段不存在")
        statement, parameters = scoped_select(
            self.table,
            scope_values(self.table, self.scope),
            filters,
            include_deleted=self.include_deleted,
        )
        return [dict(row) for row in (await connection.execute(statement, parameters)).mappings()]

    async def find_many(
        self, connection: AsyncConnection, field: str, values: Iterable[Any], **filters: Any
    ) -> list[dict[str, Any]]:
        """只读取本批关联键，分批限制参数量并保留完整范围过滤。"""
        if field not in self.table.c or set(filters) - set(self.table.c.keys()):
            raise ValueError("筛选字段不存在")
        identifiers = list(dict.fromkeys(values))
        result: list[dict[str, Any]] = []
        for start in range(0, len(identifiers), 500):
            statement, parameters = scoped_select(
                self.table,
                scope_values(self.table, self.scope),
                filters,
                {field: identifiers[start : start + 500]},
                include_deleted=self.include_deleted,
            )
            result.extend(
                dict(row) for row in (await connection.execute(statement, parameters)).mappings()
            )
        return result

    async def get_many(
        self, connection: AsyncConnection, identifiers: Iterable[str]
    ) -> dict[str, dict[str, Any]]:
        rows = await self.find_many(connection, "id", identifiers)
        result = {row["id"]: row for row in rows}
        if len(result) != len(rows):
            raise ServiceError("STORAGE_INVARIANT_BROKEN", "记录标识重复，请联系管理员", 503)
        return result

    def _validate(self, values: Mapping[str, Any]) -> None:
        validate_row(self.table, values)

    async def add(
        self,
        uow: UnitOfWork,
        record_id: str,
        values: Mapping[str, Any],
        *,
        pending: InsertBatch | None = None,
    ) -> dict[str, Any]:
        uow.require_scope(self.scope)
        uow.require_lock(record_key(self.scope.channel_id, self.table.name, record_id))
        protected = {
            "id",
            "channel_id",
            "environment",
            "subject_type",
            "subject_id",
            "created_at",
            "updated_at",
            "revision",
        }
        if set(values) & protected:
            raise ServiceError("CONTEXT_OVERRIDE", "不能通过正文覆盖归属或服务元数据", 422)
        if pending is None:
            existing = await uow.connection.scalar(
                active_rows(
                    select(self.table.c.id)
                    .where(
                        self.table.c.channel_id == self.scope.channel_id,
                        self.table.c.id == record_id,
                    )
                    .limit(1),
                    include_deleted=True,
                )
            )
            if existing is not None:
                raise ServiceError("DUPLICATE_ID", "记录标识已存在")
        now = utcnow()
        row = {
            "is_deleted": False,
            **values,
            **scope_values(self.table, self.scope),
            "id": record_id,
            "created_at": now,
            "updated_at": now,
            "revision": 1,
        }
        # 可空的范围字段也由服务上下文明确写入。
        for name in ("environment", "subject_type", "subject_id"):
            if name in self.table.c and name not in row:
                row[name] = None
        self._validate(row)
        if pending is None:
            await uow.connection.execute(insert(self.table).values(**row))
        else:
            if pending.uow is not uow:
                raise RuntimeError("批量新增不能跨工作单元")
            pending.stage(self.table, row)
        return row

    async def add_many(
        self,
        uow: UnitOfWork,
        records: Mapping[str, Mapping[str, Any]],
        *,
        pending: InsertBatch | None = None,
    ) -> dict[str, dict[str, Any]]:
        """在声明的全部记录锁内批量查重和插入；任一记录无效则整批不写入。"""
        uow.require_scope(self.scope)
        protected = {
            "id",
            "channel_id",
            "environment",
            "subject_type",
            "subject_id",
            "created_at",
            "updated_at",
            "revision",
        }
        prepared = {}
        now = utcnow()
        for identifier, values in records.items():
            uow.require_lock(record_key(self.scope.channel_id, self.table.name, identifier))
            if set(values) & protected:
                raise ServiceError("CONTEXT_OVERRIDE", "不能通过正文覆盖归属或服务元数据", 422)
            row = {
                "is_deleted": False,
                **values,
                **scope_values(self.table, self.scope),
                "id": identifier,
                "created_at": now,
                "updated_at": now,
                "revision": 1,
            }
            for name in ("environment", "subject_type", "subject_id"):
                if name in self.table.c and name not in row:
                    row[name] = None
            self._validate(row)
            prepared[identifier] = row
        if pending is not None:
            if pending.uow is not uow:
                raise RuntimeError("批量新增不能跨工作单元")
            for row in prepared.values():
                pending.stage(self.table, row)
            return prepared
        identifiers = list(prepared)
        for start in range(0, len(identifiers), 500):
            existing = await uow.connection.scalar(
                active_rows(
                    select(self.table.c.id)
                    .where(
                        self.table.c.channel_id == self.scope.channel_id,
                        self.table.c.id.in_(identifiers[start : start + 500]),
                    )
                    .limit(1),
                    include_deleted=True,
                )
            )
            if existing is not None:
                raise ServiceError("DUPLICATE_ID", "记录标识已存在")
        batch_rows = list(prepared.values())
        for start in range(0, len(batch_rows), 100):
            await uow.connection.execute(insert(self.table), batch_rows[start : start + 100])
        return prepared

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
        """管理删除统一保留记录；敏感内容清理须显式使用 purge。"""
        uow.require_scope(self.scope)
        uow.require_lock(record_key(self.scope.channel_id, self.table.name, record_id))
        await uow.connection.execute(
            soft_delete(self.table).where(self.predicate(), self.table.c.id == record_id)
        )

    async def purge(self, uow: UnitOfWork, record_id: str) -> None:
        """只用于已授权的敏感内容和到期数据清理。"""
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
                    active_rows(
                        select(self.table).where(
                            self.table.c.channel_id == "system", self.table.c[column] == value
                        )
                    )
                )
            )
            .mappings()
            .all()
        )
        if len(rows) > 1:
            raise ServiceError("STORAGE_INVARIANT_BROKEN", "控制记录重复", 503)
        return dict(rows[0]) if rows else None
