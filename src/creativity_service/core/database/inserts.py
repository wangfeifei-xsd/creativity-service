"""把同一事务中已校验的新记录集中查重和插入，供受理快照与运行共用。"""

from typing import TYPE_CHECKING, Any

from sqlalchemy import Table, insert, literal, select, union_all

from creativity_service.core.primitives import ServiceError

if TYPE_CHECKING:
    from creativity_service.core.database import UnitOfWork


class InsertBatch:
    def __init__(self, uow: "UnitOfWork") -> None:
        self.uow = uow
        self.records: dict[Table, dict[str, dict[str, Any]]] = {}

    def stage(self, table: Table, row: dict[str, Any]) -> None:
        """调用方仓储先验证业务锁和字段；此处再次约束工作单元及记录归属。"""
        if not self.uow.active or not self.uow.connection.in_transaction():
            raise RuntimeError("批量新增必须使用活动事务")
        if row["channel_id"] != self.uow.scope.channel_id:
            raise ServiceError("SCOPE_MISMATCH", "批量记录渠道与事务不符", 403)
        records = self.records.setdefault(table, {})
        if row["id"] in records:
            raise ServiceError("DUPLICATE_ID", "批量新增标识重复", 409)
        records[row["id"]] = row

    async def flush(self) -> None:
        if not self.uow.active or not self.uow.connection.in_transaction():
            raise RuntimeError("批量新增必须使用活动事务")
        pending = [
            (table, row) for table, records in self.records.items() for row in records.values()
        ]
        for start in range(0, len(pending), 100):
            batch = pending[start : start + 100]
            grouped: dict[Table, list[dict[str, Any]]] = {}
            for table, row in batch:
                grouped.setdefault(table, []).append(row)
            checks = [
                select(table.c.id).where(
                    table.c.channel_id == self.uow.scope.channel_id,
                    table.c.id.in_([row["id"] for row in records]),
                )
                for table, records in grouped.items()
            ]
            if await self.uow.connection.scalar(union_all(*checks).limit(1)) is not None:
                raise ServiceError("DUPLICATE_ID", "批量新增标识已存在", 409)
            # CTE 仅合并普通 INSERT；全部业务检查已在服务层完成，没有数据库业务分支。
            writes = [
                insert(table).values(**records[0]).cte(f"new_{table.name}")
                for table, records in grouped.items()
                if len(records) == 1
            ]
            if writes:
                await self.uow.connection.execute(select(literal(True)).add_cte(*writes))
            # 同表多行走驱动批量参数，避免为每条来源边构造一棵 INSERT 编译树。
            for table, records in grouped.items():
                if len(records) > 1:
                    await self.uow.connection.execute(insert(table), records)
        self.records.clear()
