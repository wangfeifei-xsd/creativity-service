"""显式迁移命令入口，在线与离线执行共用版本存储及事务锁。"""

from alembic.script import ScriptDirectory
from sqlalchemy import create_engine, text
from sqlalchemy.pool import NullPool

from alembic import context
from creativity_service.core.config import Settings
from creativity_service.core.migrations import MIGRATION_LOCK_KEY, ChannelPostgresqlImpl
from creativity_service.storage import metadata

target_metadata = metadata


def run_migrations() -> None:
    scripts = ScriptDirectory.from_config(context.config)
    if not scripts.get_heads():
        print("当前没有数据库修订，无需迁移。")
        return
    database_url = Settings().database_url.get_secret_value()
    options = {
        "target_metadata": target_metadata,
        "version_table": "creativity_alembic_version",
        "version_table_schema": context.config.get_main_option("version_table_schema") or None,
        "version_table_pk": False,
        "compare_type": True,
    }
    # 导入时登记 PostgreSQL 实现，使在线与离线命令使用同一个版本表定义。
    assert ChannelPostgresqlImpl.__dialect__ == "postgresql"
    if context.is_offline_mode():
        context.configure(url=database_url, literal_binds=True, **options)
        with context.begin_transaction():
            context.execute(f"SELECT pg_advisory_xact_lock({MIGRATION_LOCK_KEY})")
            context.run_migrations()
        return
    supplied_connection = context.config.attributes.get("connection")
    if supplied_connection is not None:
        supplied_connection.execute(
            text("SELECT pg_advisory_xact_lock(:key)"), {"key": MIGRATION_LOCK_KEY}
        )
        context.configure(connection=supplied_connection, **options)
        with context.begin_transaction():
            context.run_migrations()
        return
    engine = create_engine(database_url, poolclass=NullPool)
    try:
        # 所有迁移入口以同一个事务锁串行运行，版本检查和 DDL 共用事务。
        with engine.begin() as connection:
            connection.execute(
                text("SELECT pg_advisory_xact_lock(:key)"), {"key": MIGRATION_LOCK_KEY}
            )
            context.configure(connection=connection, **options)
            with context.begin_transaction():
                context.run_migrations()
    finally:
        engine.dispose()


run_migrations()
