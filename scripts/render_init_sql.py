"""离线生成表结构与冻结初始化数据的两份 SQL，不读取部署配置或当前数据库。"""

import argparse
import json
from datetime import datetime
from pathlib import Path
from typing import Any

from alembic.config import Config
from alembic.runtime.migration import MigrationContext
from alembic.script import ScriptDirectory
from sqlalchemy import Table, cast, func, insert, literal, select
from sqlalchemy.dialects import postgresql
from sqlalchemy.schema import CreateIndex, CreateTable, SetColumnComment, SetTableComment
from sqlalchemy.sql import Executable

from creativity_service.core.database import validate_row
from creativity_service.core.database.audit import audit_catalog, audit_definitions, check_sql
from creativity_service.core.migrations import MIGRATION_LOCK_KEY, SYSTEM_CHANNEL_ID
from creativity_service.modules.iam.menus import MenuService, compatible
from creativity_service.modules.iam.roles import ACTION_NAMES
from creativity_service.storage import metadata

ROOT = Path(__file__).resolve().parents[1]
ARCHIVE = ROOT / "sql/init.sql"
DATA_ARCHIVE = ROOT / "sql/init_data.sql"
SEED = ROOT / "sql/init_data.json"
SEED_TABLES = ("channels", "iam_menus", "builtin_roles", "platform_accounts")


def model() -> tuple[str, str, Table]:
    failures = audit_definitions() + audit_catalog(ROOT)
    if failures:
        raise ValueError("\n".join(failures))
    scripts = ScriptDirectory.from_config(Config(str(ROOT / "alembic.ini")))
    heads = scripts.get_heads()
    if len(heads) != 1:
        raise ValueError("初始化归档要求迁移链具有且仅具有一个最新修订")
    catalog = json.loads((ROOT / "docs/data-model/catalog.json").read_text(encoding="utf-8"))
    # 导入迁移模块后使用已登记的实现，复用系统渠道版本表定义。
    context = MigrationContext.configure(dialect_name="postgresql")
    version = context.impl.version_table_impl(
        version_table="creativity_alembic_version",
        version_table_schema=None,
        version_table_pk=False,
    )
    return catalog["model_version"], heads[0], version


def append(lines: list[str], statement: Executable) -> None:
    compiled = str(
        statement.compile(dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True})
    ).strip()
    lines.append("\n".join(line.rstrip() for line in compiled.splitlines()) + ";")
    lines.append("")


def finish(lines: list[str]) -> str:
    result = "\n".join([*lines, "COMMIT;", ""])
    failures = check_sql(result)
    if failures:
        raise ValueError("\n".join(failures))
    return result


def transaction_header() -> list[str]:
    return [
        "",
        "BEGIN;",
        "SET LOCAL standard_conforming_strings = on;",
        f"SELECT pg_advisory_xact_lock({MIGRATION_LOCK_KEY});",
        "",
    ]


def render() -> str:
    model_version, revision, version = model()
    tables = [version, *metadata.sorted_tables]
    lines = [
        "-- Creativity 表结构初始化归档，适用于 PostgreSQL 17 空库或空 schema。",
        f"-- 模型版本：{model_version}；配套数据归档迁移基线：{revision}。",
        "-- 初始建库基线：alembic/versions/0001_initial.py；后续修订在其上追加。",
        f"-- 包含 {len(tables)} 张表、{sum(len(t.c) for t in tables)} 个字段、"
        f"{sum(len(t.indexes) for t in tables)} 个普通索引及全部中文注释。",
        "-- 本文件不写初始化数据；完成后必须执行 sql/init_data.sql，再启动服务或迁移。",
        "-- 生成命令：make sql；一致性检查：make sql-check。请勿手工修改生成内容。",
        "-- 执行方式见 sql/README.md；表创建在连接的当前 schema。",
        "-- 已有同名表时整个建表事务失败回滚；不要覆盖已有库。",
        *transaction_header(),
    ]
    for table in tables:
        lines.append(f"-- {table.name}：{table.comment}。")
        append(lines, CreateTable(table))
        append(lines, SetTableComment(table))
        for column in table.c:
            append(lines, SetColumnComment(column))
        for index in sorted(table.indexes, key=lambda item: item.name or ""):
            append(lines, CreateIndex(index))
    return finish(lines)


def validate_seed(seed: dict[str, Any]) -> None:
    """生成前验证控制面身份和关联，不把初始化校验写成数据库业务约束。"""
    tables = seed["tables"]
    if set(tables) != set(SEED_TABLES):
        raise ValueError("初始化数据只能包含系统渠道、菜单、内置角色和初始管理员")
    # 仅用冻结采集时间校验字段类型；真实初始化时间仍由导入事务显式赋值。
    timestamp = datetime.fromisoformat(seed["source"]["captured_at"])
    for name in SEED_TABLES:
        rows = tables[name]
        if not rows or len({row["id"] for row in rows}) != len(rows):
            raise ValueError(f"{name} 初始数据缺失或标识重复")
        fields = set(metadata.tables[name].c.keys()) - {"created_at", "updated_at"}
        if name == "platform_accounts":
            fields.remove("credential_updated_at")
        if any(set(row) != fields or row["channel_id"] != SYSTEM_CHANNEL_ID for row in rows):
            raise ValueError(f"{name} 字段与模型不一致或不是系统渠道记录")
        for row in rows:
            times = {"created_at": timestamp, "updated_at": timestamp}
            if name == "platform_accounts":
                times["credential_updated_at"] = timestamp
            validate_row(metadata.tables[name], {**row, **times})
    if len(tables["channels"]) != 1 or tables["channels"][0]["id"] != SYSTEM_CHANNEL_ID:
        raise ValueError("只归档一个系统渠道，不复制业务渠道")
    menus = {row["id"]: row for row in tables["iam_menus"]}
    for menu in menus.values():
        MenuService.validate(menu, menus)
    roles = {row["role_code"]: row for row in tables["builtin_roles"]}
    if len(roles) != len(tables["builtin_roles"]):
        raise ValueError("初始角色编码重复")
    for role in roles.values():
        if set(role["allowed_actions"]) - ACTION_NAMES.keys():
            raise ValueError("初始角色含未知动作")
        selected = role["menu_ids"]
        if selected is not None and (
            len(set(selected)) != len(selected)
            or any(
                key not in menus or not compatible(menus[key]["workspace"], role["grant_scope"])
                for key in selected
            )
        ):
            raise ValueError("初始角色菜单关联重复、不存在或作用域不匹配")
    for code, scope in (("platform_admin", "platform"), ("channel_admin", "channel")):
        role = roles.get(code)
        if (
            not role
            or role["grant_scope"] != scope
            or not role["account_assignable"]
            or not role["menu_ids"]
        ):
            raise ValueError("两种管理员及其显式菜单关联必须归档")
    accounts = tables["platform_accounts"]
    if len(accounts) != 1 or any(
        accounts[0][key] != value
        for key, value in {
            "login_name": "admin",
            "role_id": "platform_admin",
            "platform_roles": ["platform_admin"],
            "status": "ACTIVE",
            "must_change_password": True,
            "credential_version": 1,
        }.items()
    ):
        raise ValueError("只归档启用且首次改密的 admin 平台管理员及角色关联")


def render_data() -> str:
    model_version, revision, version = model()
    seed = json.loads(SEED.read_text(encoding="utf-8"))
    validate_seed(seed)
    lines = [
        "-- Creativity 初始化数据归档，必须在配套 sql/init.sql 建表后执行。",
        f"-- 模型版本：{model_version}；完成后登记迁移基线：{revision}。",
        "-- 数据源：sql/init_data.json，冻结当前环境的系统渠道、菜单及角色配置。",
        "-- 包含 admin 账号、两种管理员的菜单关联、账号角色关联及历史兼容角色。",
        "-- 初始密码仅存安全摘要，首次登录须改密；不复制其他账号、业务授权或凭据。",
        "-- 生成命令：make sql；一致性检查：make sql-check。请勿手工修改生成内容。",
        "-- 所有数据和迁移标记在一个事务提交；已有版本记录时整份数据不再插入。",
        "-- 仅配套新库建表，不用于覆盖已有库，也不重置已有账号密码。",
        *transaction_header(),
    ]
    # 归档部署与迁移共用互斥；版本记录最后写入，避免重复导入产生无约束的重复行。
    pending = ~select(version).exists()
    descriptions = {
        "channels": "初始化平台系统渠道，不赋予业务访问权。",
        "iam_menus": "初始化当前环境菜单目录，保留层级、页面、按钮及启停排序。",
        "builtin_roles": "初始化数据库角色目录，menu_ids 保存角色与菜单关联。",
        "platform_accounts": "初始化 admin；role_id 与 platform_roles 保存账号与角色关联。",
    }
    for name in SEED_TABLES:
        lines.append(f"-- {descriptions[name]}")
        table = metadata.tables[name]
        for row in sorted(seed["tables"][name], key=lambda item: item["id"]):
            values = []
            for column in table.c:
                if column.name in {"created_at", "updated_at", "credential_updated_at"}:
                    values.append(func.current_timestamp())
                elif isinstance(column.type, postgresql.JSONB) and row[column.name] is not None:
                    values.append(
                        cast(
                            literal(
                                json.dumps(row[column.name], ensure_ascii=False, sort_keys=True)
                            ),
                            postgresql.JSONB,
                        )
                    )
                else:
                    values.append(cast(literal(row[column.name], type_=column.type), column.type))
            append(
                lines,
                insert(table).from_select(list(table.c.keys()), select(*values).where(pending)),
            )
    lines.append("-- 最后登记迁移完成标记；失败回滚时不留下半份初始化数据。")
    append(
        lines,
        insert(version).from_select(
            ["version_num", "channel_id"],
            select(literal(revision), literal(SYSTEM_CHANNEL_ID)).where(pending),
        ),
    )
    return finish(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description="生成或校验表结构和初始化数据 SQL 归档")
    parser.add_argument(
        "--check", action="store_true", help="只检查两份归档是否与模型及冻结数据一致"
    )
    args = parser.parse_args()
    outputs = {ARCHIVE: render(), DATA_ARCHIVE: render_data()}
    if args.check:
        stale = [
            str(path.relative_to(ROOT))
            for path, content in outputs.items()
            if not path.exists() or path.read_text(encoding="utf-8") != content
        ]
        if stale:
            raise SystemExit(f"初始化 SQL 归档缺失或过期：{', '.join(stale)}，请执行 make sql")
        print("表结构与初始化数据 SQL 归档检查通过")
    else:
        for path, content in outputs.items():
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content, encoding="utf-8")
            print(f"初始化 SQL 已归档：{path.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
