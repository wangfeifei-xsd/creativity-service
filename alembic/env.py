"""MySQL 迁移入口；DDL 隐式提交，连接命名锁覆盖整个迁移过程。"""

from sqlalchemy import create_engine, text
from sqlalchemy.pool import NullPool

from alembic import context
from creativity_service.core.config import Settings
from creativity_service.core.migrations import ChannelMySQLImpl
from creativity_service.storage import metadata


def run_migrations() -> None:
    options = {
        "target_metadata": metadata,
        "version_table": "creativity_alembic_version",
        "version_table_schema": context.config.get_main_option("version_table_schema") or None,
        "version_table_pk": False,
        "compare_type": True,
    }
    assert ChannelMySQLImpl.__dialect__ == "mysql"
    if context.is_offline_mode():
        context.configure(dialect_name="mysql", literal_binds=True, **options)
        context.execute("SELECT GET_LOCK(SHA2(CONCAT('creativity:migrate:', DATABASE()), 256), -1)")
        context.run_migrations()
        context.execute("COMMIT")
        context.execute("SELECT RELEASE_LOCK(SHA2(CONCAT('creativity:migrate:', DATABASE()), 256))")
        return

    def migrate(connection):
        if connection.dialect.name != "mysql":
            raise RuntimeError("当前迁移只支持 MySQL 8，新库迁移不能对旧 PostgreSQL 执行")
        schema = options["version_table_schema"]
        if schema:
            connection.exec_driver_sql(
                "USE " + connection.dialect.identifier_preparer.quote(schema)
            )
        lock = connection.scalar(
            text("SELECT SHA2(CONCAT('creativity:migrate:', DATABASE()), 256)")
        )
        if connection.scalar(text("SELECT GET_LOCK(:name, 60)"), {"name": lock}) != 1:
            raise RuntimeError("无法取得迁移锁")
        try:
            context.configure(connection=connection, **options)
            context.run_migrations()
            connection.commit()
        except BaseException:
            connection.rollback()
            raise
        finally:
            connection.execute(text("SELECT RELEASE_LOCK(:name)"), {"name": lock})

    supplied = context.config.attributes.get("connection")
    if supplied is not None:
        migrate(supplied)
        return
    engine = create_engine(Settings().database_url.get_secret_value(), poolclass=NullPool)
    try:
        with engine.connect() as connection:
            migrate(connection)
    finally:
        engine.dispose()


run_migrations()
