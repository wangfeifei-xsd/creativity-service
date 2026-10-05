"""从当前存储模型生成确定的空库初始化 SQL，不连接数据库或加载部署配置。"""

import argparse
import json
from pathlib import Path

from alembic.config import Config
from alembic.runtime.migration import MigrationContext
from alembic.script import ScriptDirectory
from sqlalchemy import cast, func, insert, literal
from sqlalchemy.dialects import postgresql
from sqlalchemy.schema import CreateIndex, CreateTable, SetColumnComment, SetTableComment
from sqlalchemy.sql import Executable

from creativity_service.core.database.audit import audit_catalog, audit_definitions, check_sql
from creativity_service.core.migrations import MIGRATION_LOCK_KEY, SYSTEM_CHANNEL_ID
from creativity_service.modules.channels.initialization import system_channel_values
from creativity_service.storage import metadata

ROOT = Path(__file__).resolve().parents[1]
ARCHIVE = ROOT / "sql/init.sql"


def render() -> str:
    failures = audit_definitions() + audit_catalog(ROOT)
    if failures:
        raise ValueError("\n".join(failures))
    scripts = ScriptDirectory.from_config(Config(str(ROOT / "alembic.ini")))
    heads = scripts.get_heads()
    if len(heads) != 1:
        raise ValueError("初始化归档要求迁移链具有且仅具有一个最新修订")
    revision = heads[0]
    catalog = json.loads((ROOT / "docs/data-model/catalog.json").read_text(encoding="utf-8"))
    # 导入迁移模块后使用已登记的实现，复用 Alembic 的系统渠道版本表定义。
    context = MigrationContext.configure(dialect_name="postgresql")
    version = context.impl.version_table_impl(
        version_table="creativity_alembic_version",
        version_table_schema=None,
        version_table_pk=False,
    )
    tables = [version, *metadata.sorted_tables]
    dialect = postgresql.dialect()
    lines = [
        "-- Creativity 全量初始化归档，适用于 PostgreSQL 17 空库或空 schema。",
        f"-- 模型版本：{catalog['model_version']}；迁移基线：{revision}。",
        "-- 初始建库基线：alembic/versions/0001_initial.py；后续修订在其上追加。",
        f"-- 包含 {len(tables)} 张表、{sum(len(t.c) for t in tables)} 个字段、"
        f"{sum(len(t.indexes) for t in tables)} 个普通索引及全部中文注释。",
        "-- 生成命令：make sql；一致性检查：make sql-check。请勿手工修改生成内容。",
        "-- 执行方式与管理员初始化见 sql/README.md；表创建在连接的当前 schema。",
        "-- 已有同名表时整个事务失败回滚；系统渠道和迁移基线与建表一起提交。",
        "",
        "BEGIN;",
        "SET LOCAL standard_conforming_strings = on;",
        f"SELECT pg_advisory_xact_lock({MIGRATION_LOCK_KEY});",
        "",
    ]

    def append(statement: Executable) -> None:
        compiled = str(
            statement.compile(dialect=dialect, compile_kwargs={"literal_binds": True})
        ).strip()
        lines.append("\n".join(line.rstrip() for line in compiled.splitlines()) + ";")
        lines.append("")

    for table in tables:
        lines.append(f"-- {table.name}：{table.comment}。")
        append(CreateTable(table))
        append(SetTableComment(table))
        for column in table.c:
            append(SetColumnComment(column))
        for index in sorted(table.indexes, key=lambda item: item.name or ""):
            append(CreateIndex(index))

    lines.append("-- 初始化平台系统渠道；管理员与内置角色由账号初始化服务创建。")
    channel = metadata.tables["channels"]
    values = system_channel_values()
    for name, value in values.items():
        if isinstance(channel.c[name].type, postgresql.JSONB):
            values[name] = cast(
                literal(json.dumps(value, ensure_ascii=False, sort_keys=True)), postgresql.JSONB
            )
    append(
        insert(channel).values(
            id=SYSTEM_CHANNEL_ID,
            channel_id=SYSTEM_CHANNEL_ID,
            created_at=func.current_timestamp(),
            updated_at=func.current_timestamp(),
            revision=1,
            **values,
        )
    )
    lines.append("-- 写入已完成的迁移基线，后续升级从此修订继续。")
    append(version.insert().values(version_num=revision))
    lines += ["COMMIT;", ""]
    result = "\n".join(lines)
    failures = check_sql(result)
    if failures:
        raise ValueError("\n".join(failures))
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description="生成或校验完整初始化 SQL 归档")
    parser.add_argument("--check", action="store_true", help="只检查归档是否与当前模型一致")
    args = parser.parse_args()
    content = render()
    if args.check:
        if not ARCHIVE.exists() or ARCHIVE.read_text(encoding="utf-8") != content:
            raise SystemExit("初始化 SQL 归档缺失或过期，请执行 make sql 并提交 sql/init.sql")
        print("初始化 SQL 归档检查通过")
    else:
        ARCHIVE.parent.mkdir(parents=True, exist_ok=True)
        ARCHIVE.write_text(content, encoding="utf-8")
        print(f"初始化 SQL 已归档：{ARCHIVE.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
