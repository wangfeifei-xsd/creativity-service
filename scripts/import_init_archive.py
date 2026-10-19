"""向专用空库一次导入结构、配置、独立删除清单和完整技能对象。"""

import argparse
import asyncio
import re
from typing import Any

from pymysql.constants import CLIENT
from sqlalchemy import Engine, create_engine, inspect
from sqlalchemy.engine import make_url

from creativity_service.core.config import Settings
from creativity_service.core.deletion.ledger import DeletionLedger
from scripts.render_init_sql import ARCHIVE, DATA_ARCHIVE, render, render_data
from scripts.restore_init_objects import archived_objects, restore_archive


def execute_script(engine: Engine, script: str) -> None:
    """逐条消费 MySQL 批量语句，遇错立即回滚数据事务，不能继续写迁移标记。"""
    raw = engine.raw_connection()
    try:
        cursor = raw.cursor()
        try:
            cursor.execute(script)
            while cursor.nextset():
                pass
        finally:
            cursor.close()
        raw.commit()
    except BaseException:
        raw.rollback()
        # 命名锁不随回滚释放，失败连接不能带着导入锁返回连接池。
        raw.invalidate()
        raise
    finally:
        raw.close()


def require_empty_ledger(manifest: dict[str, Any]) -> None:
    if manifest["blocked"] or manifest["entries"]:
        raise ValueError("目标删除清单已有删除意图或处于封锁状态，请使用既有备份恢复流程")


async def initialize(settings: Settings, engine: Engine, ledger: DeletionLedger) -> None:
    """只初始化全新数据库，既有库与已有删除意图均不能被归档覆盖。"""
    with engine.connect() as connection:
        if not str(connection.exec_driver_sql("SELECT VERSION()").scalar()).startswith("8."):
            raise ValueError("初始化归档需要 MySQL 8")
        if inspect(connection).get_table_names():
            raise ValueError("目标数据库不是空库；未执行初始化，请勿用归档覆盖已有库")
    schema, data = ARCHIVE.read_text(encoding="utf-8"), DATA_ARCHIVE.read_text(encoding="utf-8")
    if schema != render() or data != render_data():
        raise ValueError("初始化归档已过期，请先执行 make sql")
    items = archived_objects(DATA_ARCHIVE)
    channels = sorted({item["channel_id"] for item in items})
    for channel in channels:
        require_empty_ledger(await ledger.operate(channel))
    execute_script(engine, schema)
    execute_script(engine, data)
    for channel in channels:
        require_empty_ledger(await ledger.operate(channel, initialize=True))
    await restore_archive(settings, engine, items, check_only=False)
    for channel in channels:
        require_empty_ledger(await ledger.operate(channel, required=True))
    print(f"初始化完成：数据库结构、配置、删除清单和 {len(items)} 个技能包已就绪")


def main() -> None:
    parser = argparse.ArgumentParser(description="一次导入配置归档，仅适用于专用 MySQL 空库")
    parser.add_argument("--schema", help="覆盖环境配置中的数据库名")
    args = parser.parse_args()
    if args.schema and not re.fullmatch(r"[a-zA-Z_][a-zA-Z0-9_]*", args.schema):
        parser.error("schema 标识不正确")
    settings = Settings()
    url = make_url(settings.database_url.get_secret_value())
    engine = create_engine(
        url.set(database=args.schema) if args.schema else url,
        connect_args={"client_flag": CLIENT.MULTI_STATEMENTS},
        hide_parameters=True,
    )
    try:
        asyncio.run(initialize(settings, engine, DeletionLedger()))
    except Exception as exc:
        # SQL 包含冻结凭据，不向终端回显失败语句或连接信息。
        reason = str(exc) if isinstance(exc, ValueError) else type(exc).__name__
        raise SystemExit(f"初始化未完成：{reason}。请参照 sql/README.md 排查和续做。") from None
    finally:
        engine.dispose()


if __name__ == "__main__":
    main()
