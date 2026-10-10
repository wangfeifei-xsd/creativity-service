"""真实 MySQL 验证公共软删除、隔离、关联查询和旧数据升级。"""

import pytest
from sqlalchemy import func, select

from creativity_service.core.database import Repository, transaction
from creativity_service.core.database.queries import latest_per_group
from creativity_service.core.database.soft_delete import active_rows
from creativity_service.core.locking import record_key
from creativity_service.core.primitives import ServiceError
from creativity_service.modules.prompts.tables import metadata

pytestmark = pytest.mark.integration


async def test_soft_delete_preserves_row_and_blocks_read_write_and_id_reuse(engine, context):
    table = metadata.tables["prompts"]
    scope = context.scope
    repo = Repository(table, scope)
    history = Repository(table, scope, include_deleted=True)
    key = record_key(scope.channel_id, table.name, "same-id")
    values = {
        "prompt_code": "same-code",
        "name": "测试提示词",
        "purpose": "验证删除",
        "owner": "owner",
        "status": "ACTIVE",
    }
    async with transaction(engine, scope, [key]) as uow:
        row = await repo.add(uow, "same-id", values)
        assert row["is_deleted"] is False
        await repo.remove(uow, "same-id")
    async with engine.connect() as connection:
        assert await repo.get(connection, "same-id") is None
        assert await repo.find(connection) == []
        assert await repo.get_many(connection, ["same-id"]) == {}
        deleted = await history.get(connection, "same-id")
        assert deleted["is_deleted"] is True
        assert deleted["name"] == "测试提示词" and deleted["revision"] == 2
    async with transaction(engine, scope, [key]) as uow:
        with pytest.raises(ServiceError, match="记录不存在"):
            await repo.change(uow, "same-id", 2, {"name": "不能恢复"})
        with pytest.raises(ServiceError, match="标识已存在"):
            await repo.add(uow, "same-id", values)
        await repo.remove(uow, "same-id")
        assert (await history.get(uow.connection, "same-id"))["revision"] == 2


async def test_filter_applies_before_paging_count_window_and_outer_join(engine, context):
    table = metadata.tables["prompts"]
    scope = context.scope
    repo = Repository(table, scope)
    identifiers = ["left", "deleted", "right"]
    keys = [record_key(scope.channel_id, table.name, identifier) for identifier in identifiers]
    async with transaction(engine, scope, keys) as uow:
        for identifier in identifiers:
            await repo.add(
                uow,
                identifier,
                {
                    "prompt_code": identifier,
                    "name": identifier,
                    "purpose": "验证查询",
                    "owner": "owner",
                    "status": "ACTIVE",
                },
            )
        await repo.remove(uow, "deleted")
    async with engine.connect() as connection:
        count = active_rows(
            select(func.count()).select_from(table).where(table.c.channel_id == scope.channel_id)
        )
        assert await connection.scalar(count) == 2
        page = active_rows(
            select(table.c.id)
            .where(table.c.channel_id == scope.channel_id)
            .order_by(table.c.id)
            .offset(1)
            .limit(1)
        )
        assert (await connection.execute(page)).scalars().all() == ["right"]
        alias = table.alias("association")
        joined = active_rows(
            select(table.c.id, alias.c.id.label("related"))
            .select_from(
                table.outerjoin(
                    alias, (alias.c.channel_id == table.c.channel_id) & (alias.c.id == "deleted")
                )
            )
            .where(table.c.channel_id == scope.channel_id)
        )
        assert set((await connection.execute(joined)).all()) == {("left", None), ("right", None)}
        ranked = latest_per_group(table, "owner", table.c.channel_id == scope.channel_id)
        assert (await connection.execute(ranked)).mappings().one()["id"] == "right"
        exists = active_rows(
            select(alias.c.id).where(
                alias.c.channel_id == table.c.channel_id, alias.c.id == "deleted"
            )
        ).exists()
        query = active_rows(
            select(table.c.id).where(table.c.channel_id == scope.channel_id, exists)
        )
        assert (await connection.execute(query)).all() == []
