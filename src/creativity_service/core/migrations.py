"""Alembic 控制面版本存储，不创建框架默认主键或默认值。"""

from typing import Any

from alembic.ddl.mysql import MySQLImpl
from sqlalchemy import Column, MetaData, String, Table
from sqlalchemy.sql.dml import Insert

SYSTEM_CHANNEL_ID = "system"
MIGRATION_LOCK_KEY = 71977002001


class MigrationVersionTable(Table):
    def insert(self) -> Insert:
        # 迁移版本属于平台控制数据，由服务端显式写入系统渠道。
        return super().insert().values(channel_id=SYSTEM_CHANNEL_ID)


class ChannelMySQLImpl(MySQLImpl):
    __dialect__ = "mysql"

    def version_table_impl(
        self,
        *,
        version_table: str,
        version_table_schema: str | None,
        version_table_pk: bool,
        **kw: Any,
    ) -> Table:
        return MigrationVersionTable(
            version_table,
            MetaData(),
            Column("version_num", String(64), comment="当前数据库迁移修订编号"),
            Column("channel_id", String(64), comment="迁移记录所属系统渠道"),
            schema=version_table_schema,
            comment="平台数据库迁移版本记录",
        )
