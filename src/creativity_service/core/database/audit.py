"""定义、迁移源码、实际 schema 与框架表的存储审查入口。"""

import argparse
import ast
import json
import re
from pathlib import Path
from typing import Any

from sqlalchemy import Connection, create_engine, inspect, text
from sqlalchemy.dialects import postgresql
from sqlalchemy.schema import CreateIndex, CreateTable

from creativity_service.core.database.tables import BASELINE
from creativity_service.modules.agents.tables import BASELINE as AGENT_BASELINE
from creativity_service.modules.channels.tables import CURRENT as CHANNEL_BASELINE
from creativity_service.modules.conversations.tables import BASELINE as CONVERSATION_BASELINE
from creativity_service.modules.iam.tables import BASELINE as IAM_BASELINE
from creativity_service.modules.integrations.review_tables import BASELINE as REVIEW_BASELINE
from creativity_service.modules.integrations.tables import BASELINE as INTEGRATION_BASELINE
from creativity_service.modules.mcp.tables import BASELINE as MCP_BASELINE
from creativity_service.modules.memory.tables import BASELINE as MEMORY_BASELINE
from creativity_service.modules.runs.tables import BASELINE as RUN_BASELINE
from creativity_service.modules.skills.tables import BASELINE as SKILL_BASELINE
from creativity_service.modules.tools.tables import BASELINE as TOOL_BASELINE
from creativity_service.modules.usage.tables import BASELINE as USAGE_BASELINE
from creativity_service.storage import metadata

POSTGRESQL_DIALECT: Any = postgresql.dialect

CHINESE = re.compile(r"[\u4e00-\u9fff]")
FORBIDDEN_SQL = re.compile(
    r"\b(PRIMARY\s+KEY|UNIQUE|FOREIGN\s+KEY|CHECK\s*\(|CREATE\s+(?:OR\s+REPLACE\s+)?"
    r"(?:TRIGGER|FUNCTION|PROCEDURE|POLICY)|ENABLE\s+ROW\s+LEVEL|GENERATED\s+ALWAYS|"
    r"ON\s+CONFLICT|\bDEFAULT\b)",
    re.IGNORECASE,
)


def check_sql(sql: str) -> list[str]:
    # 先去除字符串与注释，避免中文业务说明中的术语被当作 DDL。
    cleaned = re.sub(r"'(?:''|[^'])*'|--[^\n]*|/\*.*?\*/", " ", sql, flags=re.DOTALL)
    return [f"禁止的数据库定义：{match.group(0)}" for match in FORBIDDEN_SQL.finditer(cleaned)]


def audit_definitions() -> list[str]:
    failures = []
    for table in metadata.tables.values():
        if "channel_id" not in table.c or not CHINESE.search(table.comment or ""):
            failures.append(f"{table.name} 缺少渠道或中文表注释")
        for column in table.c:
            if not CHINESE.search(column.comment or ""):
                failures.append(f"{table.name}.{column.name} 缺少中文注释")
            if (
                column.default is not None
                or column.server_default is not None
                or column.computed is not None
            ):
                failures.append(f"{table.name}.{column.name} 存在隐式赋值")
        failures += check_sql(str(CreateTable(table).compile(dialect=POSTGRESQL_DIALECT())))
        for index in table.indexes:
            failures += check_sql(str(CreateIndex(index).compile(dialect=POSTGRESQL_DIALECT())))
    return failures


def audit_sources(root: Path) -> list[str]:
    failures = []
    for directory in (root / "src", root / "alembic"):
        for path in directory.rglob("*.py"):
            if path.resolve() == Path(__file__).resolve():
                continue
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if isinstance(node, ast.Call):
                    name = (
                        node.func.attr
                        if isinstance(node.func, ast.Attribute)
                        else node.func.id
                        if isinstance(node.func, ast.Name)
                        else ""
                    )
                    if name in {
                        "create_all",
                        "on_conflict_do_update",
                        "on_conflict_do_nothing",
                        "UniqueConstraint",
                        "ForeignKey",
                        "ForeignKeyConstraint",
                        "CheckConstraint",
                        "Computed",
                        "Identity",
                    }:
                        failures.append(f"{path.name}:{node.lineno} 未经允许的持久化入口 {name}")
                    if name == "setup" and "checkpoint" in path.read_text():
                        failures.append(f"{path.name}:{node.lineno} 未审核的框架自动建表")
                    for kw in node.keywords:
                        if (
                            kw.arg in {"primary_key", "unique"}
                            and isinstance(kw.value, ast.Constant)
                            and kw.value.value is True
                        ):
                            failures.append(f"{path.name}:{node.lineno} 隐式唯一索引")
                        if kw.arg == "server_default":
                            failures.append(f"{path.name}:{node.lineno} 数据库默认赋值")
                if (
                    isinstance(node, ast.Constant)
                    and isinstance(node.value, str)
                    and re.search(
                        r"\b(CREATE|ALTER)\s+(TABLE|INDEX|UNIQUE|TRIGGER|FUNCTION|POLICY)",
                        node.value,
                        re.I,
                    )
                ):
                    failures += [
                        f"{path.name}:{node.lineno} {failure}" for failure in check_sql(node.value)
                    ]
    return failures


def audit_catalog(root: Path) -> list[str]:
    catalog = json.loads((root / "docs/data-model/catalog.json").read_text(encoding="utf-8"))
    failures = []
    names = [table["name"] for table in catalog["tables"]]
    if len(names) != len(set(names)):
        failures.append("模型清单存在重复表归属")
    implemented = {
        t["name"]
        for t in catalog["tables"]
        if t["status"] == "已实现" and t["name"] != "creativity_alembic_version"
    }
    if implemented != set(metadata.tables):
        failures.append("已实现模型清单与登记元数据不一致")
    if [t for t in catalog["tables"] if t["revision"] == "0016_agents"] != AGENT_BASELINE:
        failures.append("智能体模块归档与冻结实现不一致")
    archived = [t for t in catalog["tables"] if t["revision"] == "0001_core"]
    if archived != BASELINE:
        failures.append("公共表归档与冻结实现不一致")
    if [t for t in catalog["tables"] if t["revision"] == "0002_iam"] != IAM_BASELINE:
        failures.append("账号模块归档与冻结实现不一致")
    if [t for t in catalog["tables"] if t["module"] == "channels"] != CHANNEL_BASELINE:
        failures.append("渠道模块归档与冻结实现不一致")
    if [t for t in catalog["tables"] if t["revision"] == "0010_tools"] != TOOL_BASELINE:
        failures.append("工具模块归档与冻结实现不一致")
    if [t for t in catalog["tables"] if t["revision"] == "0004_usage"] != USAGE_BASELINE:
        failures.append("用量模块归档与冻结实现不一致")
    if [t for t in catalog["tables"] if t["revision"] == "0014_mcp"] != MCP_BASELINE:
        failures.append("MCP 模块归档与冻结实现不一致")
    if [
        t for t in catalog["tables"] if t["revision"] == "0021_mcp_subject_review"
    ] != REVIEW_BASELINE:
        failures.append("主体复核归档与冻结实现不一致")
    if [t for t in catalog["tables"] if t["revision"] == "0015_skills"] != SKILL_BASELINE:
        failures.append("技能模块归档与冻结实现不一致")
    if [t for t in catalog["tables"] if t["revision"] == "0011_runs"] != RUN_BASELINE:
        failures.append("运行模块归档与冻结实现不一致")
    if [
        t for t in catalog["tables"] if t["revision"] == "0012_conversations"
    ] != CONVERSATION_BASELINE:
        failures.append("会话模块归档与冻结实现不一致")
    if [
        t for t in catalog["tables"] if t["revision"] == "0018_integrations"
    ] != INTEGRATION_BASELINE:
        failures.append("业务接入归档与冻结实现不一致")
    if [t for t in catalog["tables"] if t["revision"] == "0013_memory"] != MEMORY_BASELINE:
        failures.append("记忆模块归档与冻结实现不一致")
    for table in catalog["tables"]:
        if table["module"] not in catalog["modules"] or "channel_id" not in [
            c["name"] for c in table["columns"]
        ]:
            failures.append(f"{table['name']} 缺少归属或渠道")
    # 需求 15–17 为可选配置示例，不再要求独立平台数据模型。
    for number in range(15):
        if not any(
            info["requirements"].startswith(f"{number:02d}-")
            for info in catalog["modules"].values()
        ):
            failures.append(f"需求模块 {number:02d} 未进入数据模型基线")
    return failures


def audit_database(
    connection: Connection, schema: str = "public", *, compare: bool = True
) -> list[str]:
    inspector = inspect(connection)
    failures = []
    names = inspector.get_table_names(schema=schema)
    for name in names:
        if not CHINESE.search(inspector.get_table_comment(name, schema=schema).get("text") or ""):
            failures.append(f"{name} 缺少中文表注释")
        columns = inspector.get_columns(name, schema=schema)
        if "channel_id" not in {c["name"] for c in columns}:
            failures.append(f"{name} 缺少渠道字段")
        else:
            quoting = connection.dialect.identifier_preparer
            qualified = f"{quoting.quote_schema(schema)}.{quoting.quote(name)}"
            missing_channel = connection.scalar(
                text(
                    f"SELECT EXISTS (SELECT 1 FROM {qualified} "
                    "WHERE channel_id IS NULL OR channel_id = '')"
                )
            )
            if missing_channel:
                failures.append(f"{name} 存在缺少渠道的实际记录")
        for column in columns:
            if not CHINESE.search(column.get("comment") or ""):
                failures.append(f"{name}.{column['name']} 缺少中文字段注释")
            if (
                column.get("default") is not None
                or column.get("computed")
                or column.get("identity")
            ):
                failures.append(f"{name}.{column['name']} 存在数据库默认赋值")
        if (
            inspector.get_pk_constraint(name, schema=schema).get("constrained_columns")
            or inspector.get_foreign_keys(name, schema=schema)
            or inspector.get_check_constraints(name, schema=schema)
            or inspector.get_unique_constraints(name, schema=schema)
        ):
            failures.append(f"{name} 存在数据库业务约束")
        if any(index.get("unique") for index in inspector.get_indexes(name, schema=schema)):
            failures.append(f"{name} 存在唯一索引")
        if compare and name in metadata.tables:
            table = metadata.tables[name]
            actual = {
                c["name"]: (
                    str(c["type"].compile(dialect=connection.dialect)).lower(),
                    c["comment"],
                )
                for c in columns
            }
            expected = {
                c.name: (str(c.type.compile(dialect=POSTGRESQL_DIALECT())).lower(), c.comment)
                for c in table.c
            }
            if actual != expected:
                failures.append(f"{name} 实际字段或注释与模型不一致")
            if inspector.get_table_comment(name, schema=schema).get("text") != table.comment:
                failures.append(f"{name} 实际表注释与模型不一致")
            actual_indexes = {
                tuple(index["column_names"]) for index in inspector.get_indexes(name, schema=schema)
            }
            expected_indexes = {tuple(c.name for c in index.columns) for index in table.indexes}
            if actual_indexes != expected_indexes:
                failures.append(f"{name} 实际索引与模型不一致")
    if compare and set(metadata.tables) - set(names):
        failures.append("实际数据库缺少公共表")
    checks: dict[str, str] = {
        "触发器": "SELECT count(*) FROM pg_trigger t "
        "JOIN pg_class c ON c.oid=t.tgrelid "
        "JOIN pg_namespace n ON n.oid=c.relnamespace "
        "WHERE n.nspname=:schema AND NOT t.tgisinternal",
        "行级安全": "SELECT count(*) FROM pg_class c "
        "JOIN pg_namespace n ON n.oid=c.relnamespace "
        "WHERE n.nspname=:schema AND (c.relrowsecurity OR c.relforcerowsecurity)",
        "存储函数或过程": "SELECT count(*) FROM pg_proc p "
        "JOIN pg_namespace n ON n.oid=p.pronamespace "
        "WHERE n.nspname=:schema "
        "AND NOT EXISTS (SELECT 1 FROM pg_depend d WHERE d.objid=p.oid AND d.deptype='e')",
        "权限策略": "SELECT count(*) FROM pg_policies WHERE schemaname=:schema",
    }
    for label, query in checks.items():
        if connection.execute(text(query), {"schema": schema}).scalar_one():
            failures.append(f"实际 schema 存在{label}")
    return failures


def main() -> None:
    parser = argparse.ArgumentParser(description="检查存储定义、迁移和实际 schema")
    parser.add_argument("--database", action="store_true")
    parser.add_argument("--schema", default="public")
    parser.add_argument("--sql", type=Path)
    args = parser.parse_args()
    root = Path.cwd()
    failures = audit_definitions() + audit_sources(root) + audit_catalog(root)
    if args.sql:
        failures += check_sql(args.sql.read_text())
    if args.database:
        from creativity_service.core.config import Settings

        engine = create_engine(Settings().database_url.get_secret_value())
        try:
            with engine.connect() as connection:
                failures += audit_database(connection, args.schema)
        finally:
            engine.dispose()
    if failures:
        raise SystemExit("\n".join(failures))
    print("存储审查通过")


if __name__ == "__main__":
    main()
