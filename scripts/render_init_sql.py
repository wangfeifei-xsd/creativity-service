"""离线生成表结构与冻结初始化数据的两份 SQL，不读取部署配置或当前数据库。"""

import argparse
import json
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

from alembic.config import Config
from alembic.runtime.migration import MigrationContext
from alembic.script import ScriptDirectory
from sqlalchemy import LargeBinary, Numeric, Table, cast, func, insert, literal, select
from sqlalchemy.dialects import postgresql
from sqlalchemy.schema import CreateIndex, CreateTable, SetColumnComment, SetTableComment
from sqlalchemy.sql import Executable

from creativity_service.core.database import validate_row
from creativity_service.core.database.audit import audit_catalog, audit_definitions, check_sql
from creativity_service.core.migrations import MIGRATION_LOCK_KEY, SYSTEM_CHANNEL_ID
from creativity_service.core.primitives import ServiceError, digest
from creativity_service.modules.budgets.schemas import BudgetCreate, PlatformLimitCreate
from creativity_service.modules.channels.codes import channel_code
from creativity_service.modules.channels.initialization import system_channel_values
from creativity_service.modules.channels.schemas import RetentionPolicy
from creativity_service.modules.iam.menus import MenuService, compatible
from creativity_service.modules.iam.repositories import membership_id
from creativity_service.modules.iam.roles import ACTION_NAMES
from creativity_service.modules.models.policy import validate_endpoint
from creativity_service.modules.models.schemas import ProviderInput
from creativity_service.modules.usage.pricing import timezone
from creativity_service.storage import metadata
from scripts.render_init_mcp import validate_initial_mcp
from scripts.render_init_models import MODEL_SEED_TABLES, validate_initial_models
from scripts.render_weather_seed import OBJECT_MARKER, load_weather, typed_archive_values

ROOT = Path(__file__).resolve().parents[1]
ARCHIVE = ROOT / "sql/init.sql"
DATA_ARCHIVE = ROOT / "sql/init_data.sql"
SEED = ROOT / "sql/init_data.json"
SEED_TABLES = (
    "channels",
    "channel_code_index",
    "provider_catalog",
    "iam_menus",
    "builtin_roles",
    "platform_accounts",
    "channel_memberships",
    "resource_grants",
    "platform_limits",
    "resource_versions",
    "budget_policies",
    *MODEL_SEED_TABLES,
    "mcp_connections",
)
TENANT_SEED_TABLES = ("channel_memberships", "resource_grants")
TENANT_CONFIGURATION_TABLES = (
    "resource_versions",
    "budget_policies",
    *MODEL_SEED_TABLES,
    "mcp_connections",
)
IMPORT_TIME_FIELDS = {
    "platform_accounts": {"credential_updated_at"},
    "platform_limits": {"effective_at"},
    "mcp_connections": {"next_check_at"},
}


def import_time_fields(name: str) -> set[str]:
    """初始账号、限额及连接兼容时钟取导入事务时间，不继承采集时间。"""
    return {"created_at", "updated_at"} | IMPORT_TIME_FIELDS.get(name, set())


def typed_seed_values(table: Table, row: dict[str, Any]) -> dict[str, Any]:
    """冻结 JSON 用字符串保存十进制和密文，校验与 SQL 编译时显式还原类型。"""
    values = dict(row)
    for column in table.c:
        if isinstance(column.type, Numeric) and values.get(column.name) is not None:
            value = values[column.name]
            if not isinstance(value, str):
                raise ValueError("初始化十进制值必须使用字符串，不能使用浮点数")
            values[column.name] = Decimal(value)
        elif isinstance(column.type, LargeBinary) and values.get(column.name) is not None:
            value = values[column.name]
            if not isinstance(value, str):
                raise ValueError("初始化密文必须使用十六进制字符串")
            try:
                values[column.name] = bytes.fromhex(value)
            except ValueError as exc:
                raise ValueError("初始化密文必须使用十六进制字符串") from exc
    return values


def validate_initial_limits(tables: dict[str, Any], tenants: dict[str, Any], admin_id: str) -> None:
    """平台计数与渠道策略分开归属，策略版本必须匹配且不复制运行占用。"""
    platform = tables["platform_limits"]
    if len(platform) != 1:
        raise ValueError("只预置一份平台并发限额")
    row = typed_seed_values(metadata.tables["platform_limits"], platform[0])
    quantity = row["limit_value"]
    if not quantity.is_finite() or quantity <= 0 or quantity != quantity.to_integral_value():
        raise ValueError("初始平台并发上限必须为正整数")
    limit = PlatformLimitCreate.model_validate(
        {key: row[key] for key in PlatformLimitCreate.model_fields} | {"limit_value": int(quantity)}
    )
    if (
        limit.limit_code != "concurrency"
        or limit.unit != "concurrency"
        or row["kind"] != limit.unit
        or limit.status != "ACTIVE"
        or row["replaces_id"] is not None
    ):
        raise ValueError("初始平台限额必须为启用的并发初始版本")
    policies = tables["budget_policies"]
    versions = [
        row for row in tables["resource_versions"] if row["resource_type"] == "budget_policy"
    ]
    if len(policies) != len(tenants) or {row["channel_id"] for row in policies} != tenants.keys():
        raise ValueError("每个初始业务渠道须有且仅有一份并发策略")
    if len(versions) != len(policies) or {row["id"] for row in versions} != {
        row["version_id"] for row in policies
    }:
        raise ValueError("初始并发策略版本缺失、重复或关联不一致")
    versions_by_id = {row["id"]: row for row in versions}
    for row in policies:
        body = BudgetCreate.model_validate({key: row[key] for key in BudgetCreate.model_fields})
        if (
            body.scope_type != "channel"
            or body.scope_id != row["channel_id"]
            or body.unit != "concurrency"
            or body.mode != "HARD"
            or body.status != "ACTIVE"
            or body.currency is not None
            or body.limit_value <= 0
            or body.limit_value != body.limit_value.to_integral_value()
            or not body.thresholds
            or len(set(body.thresholds)) != len(body.thresholds)
            or any(not value.is_finite() or not 0 < value <= 1 for value in body.thresholds)
        ):
            raise ValueError("初始渠道并发策略归属、正整数上限或硬控制配置不合法")
        payload = body.model_dump(mode="json")
        version = versions_by_id[row["version_id"]]
        expected = {
            "channel_id": row["channel_id"],
            "resource_type": "budget_policy",
            "resource_id": row["id"],
            "state": "FROZEN",
            "content": payload,
            "content_digest": digest({"content": payload, "output_schema": {}}),
            "dependencies": [],
            "dependencies_digest": digest([]),
            "output_schema": {},
            "created_by": admin_id,
        }
        if any(version[key] != value for key, value in expected.items()):
            raise ValueError("初始渠道并发策略版本内容、摘要或归属不一致")
    try:
        for zone in {limit.timezone, *(row["timezone"] for row in policies)}:
            timezone(zone)
    except ServiceError as exc:
        raise ValueError("初始并发限额时区无效") from exc


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
    # 交付物是直接执行的 SQL 文本，使用命名参数方言避免把 URL 百分号编译成 %%；
    # 同时匹配文件头的标准字符串设置，嵌套 JSON 反斜杠不能再次转义。
    dialect = postgresql.dialect(paramstyle="named")
    dialect._backslash_escapes = False
    compiled = str(
        statement.compile(dialect=dialect, compile_kwargs={"literal_binds": True})
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
    """生成前验证渠道、身份、空范围授权和并发策略，不把校验写成数据库业务约束。"""
    tables = seed["tables"]
    if set(tables) != set(SEED_TABLES):
        raise ValueError(
            "初始化数据只能包含预置渠道、控制面配置、初始授权、并发策略及模型和 MCP 配置"
        )
    # 仅用冻结采集时间校验字段类型；真实初始化时间仍由导入事务显式赋值。
    timestamp = datetime.fromisoformat(seed["source"]["captured_at"])
    for name in SEED_TABLES:
        rows = tables[name]
        if not rows or len({row["id"] for row in rows}) != len(rows):
            raise ValueError(f"{name} 初始数据缺失或标识重复")
        fields = set(metadata.tables[name].c.keys()) - import_time_fields(name)
        if any(set(row) != fields for row in rows):
            raise ValueError(f"{name} 字段与模型不一致")
        if name not in ("channels", *TENANT_SEED_TABLES, *TENANT_CONFIGURATION_TABLES) and any(
            row["channel_id"] != SYSTEM_CHANNEL_ID for row in rows
        ):
            raise ValueError(f"{name} 不是系统渠道记录")
        for row in rows:
            times = {key: timestamp for key in import_time_fields(name)}
            validate_row(
                metadata.tables[name], {**typed_seed_values(metadata.tables[name], row), **times}
            )
    channels = {row["id"]: row for row in tables["channels"]}
    if SYSTEM_CHANNEL_ID not in channels or any(
        row["channel_id"] != row["id"] for row in channels.values()
    ):
        raise ValueError("必须包含系统渠道，渠道主档归属须等于自身标识")
    system = channels[SYSTEM_CHANNEL_ID]
    if any(system[key] != value for key, value in system_channel_values().items()):
        raise ValueError("系统渠道配置与初始化定义不一致")
    tenants = {key: row for key, row in channels.items() if key != SYSTEM_CHANNEL_ID}
    if len({row["channel_code"] for row in channels.values()}) != len(channels):
        raise ValueError("初始渠道编码重复")
    for row in tenants.values():
        if row["channel_code"] != channel_code(row["name"]):
            raise ValueError("初始渠道编码与名称生成规则不一致")
        RetentionPolicy.model_validate(row["retention_policy"])
    indexes = tables["channel_code_index"]
    if (
        len(indexes) != len(tenants)
        or {row["target_channel_id"] for row in indexes} != tenants.keys()
    ):
        raise ValueError("初始渠道目录缺失、重复或引用不存在的业务渠道")
    if any(
        row["channel_code"] != tenants[row["target_channel_id"]]["channel_code"] for row in indexes
    ):
        raise ValueError("初始渠道目录编码与渠道主档不一致")
    providers = tables["provider_catalog"]
    if len({row["code"] for row in providers}) != len(providers):
        raise ValueError("初始供应商编码重复")
    for row in providers:
        provider = ProviderInput.model_validate(
            {key: row[key] for key in ProviderInput.model_fields}
        )
        if row["id"] != "provider_" + provider.code or len(set(provider.protocols)) != len(
            provider.protocols
        ):
            raise ValueError("初始供应商标识与编码不匹配或协议重复")
        template = provider.template_content
        if (
            set(template) != {"protocol", "endpoint", "timeout_seconds"}
            or template["protocol"] not in provider.protocols
            or not isinstance(template["endpoint"], str)
        ):
            raise ValueError("初始供应商模板只能包含已登记协议、基础地址和超时，不含凭据")
        timeout = template["timeout_seconds"]
        if type(timeout) is not int or not 1 <= timeout <= 600:
            raise ValueError("初始供应商模板超时必须为 1 至 600 秒的整数")
        try:
            validate_endpoint(template["protocol"], template["endpoint"])
        except ServiceError as exc:
            raise ValueError("初始供应商模板基础地址不合法") from exc
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
    # 复用初始管理员，但不编造环境或外部映射；空范围授权不能用于业务访问。
    admin_id = accounts[0]["id"]
    for name in TENANT_SEED_TABLES:
        rows = tables[name]
        if len(rows) != len(tenants) or {row["channel_id"] for row in rows} != tenants.keys():
            raise ValueError("每个初始业务渠道须有且仅有一份首位管理员及空范围授权")
        for row in rows:
            channel_id = row["channel_id"]
            member_id = membership_id(channel_id, admin_id)
            expected = {"environments": []}
            if name == "channel_memberships":
                expected.update(
                    id=member_id,
                    user_id=admin_id,
                    roles=["channel_admin"],
                    status="ACTIVE",
                    granted_by=admin_id,
                )
            else:
                expected.update(
                    id="initial_" + member_id,
                    grantee_type="account",
                    grantee_id=admin_id,
                    resource_type="channel",
                    resource_id=channel_id,
                    allowed_actions=sorted(roles["channel_admin"]["allowed_actions"]),
                )
            if any(row[key] != value for key, value in expected.items()):
                raise ValueError("初始渠道管理员或授权关联不一致，环境必须为空")
    validate_initial_limits(tables, tenants, admin_id)
    validate_initial_models(tables, tenants, admin_id)
    validate_initial_mcp(tables, tenants, admin_id)


def render_data() -> str:
    model_version, revision, version = model()
    seed = json.loads(SEED.read_text(encoding="utf-8"))
    validate_seed(seed)
    weather = load_weather(seed["tables"])
    lines = [
        "-- Creativity 初始化数据归档，必须在配套 sql/init.sql 建表后执行。",
        f"-- 模型版本：{model_version}；完成后登记迁移基线：{revision}。",
        "-- 数据源：sql/init_data.json，冻结控制面配置、预置渠道、模型和 MCP 连接及并发策略。",
        "-- 包含 admin 账号、两种管理员的菜单关联、账号角色关联及历史兼容角色。",
        "-- 天气链路数据源：sql/weather_data.json；仅保留成功运行及必要依赖。",
        "-- 预置渠道的首位管理员复用 admin，天气示例仅开放开发环境。",
        "-- 初始密码仅存安全摘要，首次登录须改密；不复制其他账号或接入凭据。",
        "-- 平台并发上限归系统渠道，各渠道并发策略及冻结版本归对应业务渠道。",
        "-- 包含开发环境 DeepSeek V4 Flash 及密文凭据；解密主密钥和出站策略另行配置。",
        "-- 租号服务归档基本配置与 appSecret 原文，无需解密主密钥；导入后重新测试、发现与启用。",
        "-- 保留天气链路所需能力验证、真实输入输出和用量；历史身份不携带登录会话。",
        "-- 技能对象字节随本 SQL 注释封存，导入后运行 scripts.restore_init_objects 恢复对象存储。",
        "-- 生成命令：make sql；一致性检查：make sql-check。请勿手工修改生成内容。",
        "-- 所有数据和迁移标记在一个事务提交；已有版本记录时整份数据不再插入。",
        "-- 仅配套新库建表，不用于覆盖已有库，也不重置已有账号密码。",
        *transaction_header(),
    ]
    # 归档部署与迁移共用互斥；版本记录最后写入，避免重复导入产生无约束的重复行。
    pending = ~select(version).exists()
    descriptions = {
        "channels": "初始化平台系统渠道及预置业务渠道，主档分别归自身渠道。",
        "channel_code_index": "初始化系统渠道中的业务渠道目录，供列表、定位和编码查重。",
        "provider_catalog": "初始化预置供应商字典及无凭据连接模板。",
        "iam_menus": "初始化当前环境菜单目录，保留层级、页面、按钮及启停排序。",
        "builtin_roles": "初始化数据库角色目录，menu_ids 保存角色与菜单关联。",
        "platform_accounts": "初始化 admin；role_id 与 platform_roles 保存账号与角色关联。",
        "channel_memberships": "首位管理员基础成员种子；天气归档补齐开发环境。",
        "resource_grants": "初始授权基础种子；天气归档限定开发环境及成功记录。",
        "platform_limits": "初始化平台并发上限，导入时生效；不复制实际运行占用。",
        "resource_versions": "初始化各业务渠道并发策略、模型及连接的冻结版本及内容摘要。",
        "budget_policies": "初始化各业务渠道并发硬上限，不复制用量、预占或预算提醒。",
        "credentials": "初始化模型的 AES-GCM 密文凭据与 MCP 凭据原文；模型主密钥不进入 SQL。",
        "mcp_connections": "初始化开发环境租号服务的基本配置和鉴权，健康状态在目标环境重新验证。",
        "model_connections": "初始化开发环境模型连接，健康状态在目标环境重新验证。",
        "models": "初始化 DeepSeek V4 Flash 模型映射，能力状态保持未验证。",
        "resource_references": "初始化模型版本对连接版本的同渠道依赖。",
        "source_links": "初始化模型及连接的版本来源，保留删除传播关系。",
    }
    for name in SEED_TABLES:
        lines.append(f"-- {descriptions[name]}")
        table = metadata.tables[name]
        for source in sorted(seed["tables"][name], key=lambda item: item["id"]):
            if source["id"] in {row["id"] for row in weather["tables"].get(name, [])}:
                continue
            row = typed_seed_values(table, source)
            values = []
            for column in table.c:
                if column.name in import_time_fields(name):
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
                elif isinstance(column.type, LargeBinary) and row[column.name] is not None:
                    values.append(func.decode(literal(row[column.name].hex()), literal("hex")))
                else:
                    values.append(cast(literal(row[column.name], type_=column.type), column.type))
            append(
                lines,
                insert(table).from_select(list(table.c.keys()), select(*values).where(pending)),
            )
    lines.append("-- 成功天气链路：保留采集时的真实时间、发布配置、调用证据与用量。")
    for name, rows in weather["tables"].items():
        table = metadata.tables[name]
        lines.append(f"-- 天气归档 {name}：{len(rows)} 条。")
        for source in rows:
            row = typed_archive_values(table, source)
            values = []
            for column in table.c:
                value = row[column.name]
                if isinstance(column.type, postgresql.JSONB) and value is not None:
                    expression = cast(
                        literal(json.dumps(value, ensure_ascii=False, sort_keys=True)),
                        postgresql.JSONB,
                    )
                elif isinstance(column.type, LargeBinary) and value is not None:
                    expression = func.decode(literal(value.hex()), literal("hex"))
                else:
                    expression = cast(literal(value, type_=column.type), column.type)
                values.append(expression)
            append(
                lines,
                insert(table).from_select(list(table.c.keys()), select(*values).where(pending)),
            )
    for item in weather["objects"]:
        lines.append(OBJECT_MARKER + json.dumps(item, ensure_ascii=False, sort_keys=True))
    lines.append("")
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
