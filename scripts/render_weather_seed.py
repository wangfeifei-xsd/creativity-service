"""校验天气成功链路的冻结数据；不读取部署秘密或当前数据库。"""

import base64
import hashlib
import io
import json
import zipfile
from datetime import date, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

from sqlalchemy import Date, DateTime, LargeBinary, Numeric, Table

from creativity_service.core.contracts import BusinessResult
from creativity_service.core.database import validate_row
from creativity_service.core.primitives import digest
from creativity_service.storage import metadata

WEATHER_SEED = Path(__file__).resolve().parents[1] / "sql/weather_data.json"
OBJECT_MARKER = "-- creativity-object: "
WEATHER_TABLES = frozenset(
    "agents skills prompts model_routes tools mcp_connections channel_environments models "
    "model_tests runs run_steps attempts run_events run_contents tool_calls usage_records "
    "release_snapshots resource_uses prompt_tests admissions prompt_samples agent_candidates "
    "agent_release_records resource_versions model_connections credentials release_mappings "
    "resource_references mcp_discoveries mcp_imports mcp_checks skill_files artifacts "
    "evidence_refs usage_events usage_adjustments resource_grants source_links audit_events "
    "channel_memberships".split()
)
# 同一模型和初始管理员沿用原标识；天气归档只补充已验证配置及开发环境。
OVERRIDE_TABLES = frozenset(
    "models model_connections credentials resource_versions resource_references source_links "
    "channel_memberships resource_grants".split()
)


def typed_archive_values(table: Table, source: dict[str, Any]) -> dict[str, Any]:
    """还原冻结时间、精确金额和密文字节，保持真实运行发生时间。"""
    row = dict(source)
    for column in table.c:
        value = row.get(column.name)
        if value is None:
            continue
        if isinstance(column.type, DateTime):
            row[column.name] = datetime.fromisoformat(value)
        elif isinstance(column.type, Date):
            row[column.name] = date.fromisoformat(value)
        elif isinstance(column.type, Numeric):
            row[column.name] = Decimal(value)
        elif isinstance(column.type, LargeBinary):
            row[column.name] = bytes.fromhex(value)
    return row


def merged_tables(base: dict[str, Any], weather: dict[str, Any]) -> dict[str, Any]:
    """合并离线数据；重叠记录必须来自显式允许的配置表。"""
    result = {name: {r["id"]: r for r in rows} for name, rows in base.items()}
    for name, rows in weather.items():
        current = result.setdefault(name, {})
        for row in rows:
            if row["id"] in current and name not in OVERRIDE_TABLES:
                raise ValueError(f"天气归档不能覆盖 {name}")
            current[row["id"]] = row
    return {name: sorted(rows.values(), key=lambda r: r["id"]) for name, rows in result.items()}


def object_bytes(item: dict[str, Any]) -> bytes:
    """对象内容与清单必须一致，避免恢复损坏的技能包。"""
    data = base64.b64decode(item["base64"], validate=True)
    if len(data) != item["size_bytes"] or hashlib.sha256(data).hexdigest() != item["sha256"]:
        raise ValueError("天气技能对象大小或摘要不匹配")
    return data


def validate_weather(seed: dict[str, Any], base: dict[str, Any]) -> None:
    """校验范围、成功终态、引用闭合及两项真实天气断言。"""
    tables = seed["tables"]
    channel = seed["source"]["channel_id"]
    if set(tables) != WEATHER_TABLES or seed["source"]["environment"] != "dev":
        raise ValueError("天气归档表清单或环境不正确")
    for name, rows in tables.items():
        if not rows or len({r["id"] for r in rows}) != len(rows):
            raise ValueError(f"{name} 归档为空或标识重复")
        for row in rows:
            validate_row(metadata.tables[name], typed_archive_values(metadata.tables[name], row))
            if row["channel_id"] != channel or row.get("environment", "dev") != "dev":
                raise ValueError("天气归档不能混入其他渠道或环境")
    merged = merged_tables(base, tables)
    index = {name: {r["id"]: r for r in rows} for name, rows in merged.items()}
    for row in tables["resource_versions"]:
        if not set(row["dependencies"]) <= index["resource_versions"].keys():
            raise ValueError("天气版本依赖缺失")
        if row["content_digest"] != digest(
            {"content": row["content"], "output_schema": row["output_schema"]}
        ):
            raise ValueError("天气版本摘要不一致")
    for row in tables["resource_references"]:
        if (
            not {row["source_version_id"], row["target_version_id"]}
            <= index["resource_versions"].keys()
        ):
            raise ValueError("天气资源引用不完整")
    runs = index["runs"]
    contents = index["run_contents"]
    cases = seed["source"]["cases"]
    if set(cases) != {"今天的南京天气如何？", "天气如何？"}:
        raise ValueError("天气归档缺少指定问题或默认问题")
    expected_runs = (
        set(cases.values())
        | {r["run_id"] for r in tables["model_tests"]}
        | {r["run_id"] for r in tables["prompt_tests"]}
        | {r["run_id"] for r in tables["tool_calls"]}
    )
    if set(runs) != expected_runs:
        raise ValueError("天气归档混入无关运行")
    for run in runs.values():
        if run["state"] != "SUCCEEDED" or not run["resources_released"] or run["error"]:
            raise ValueError("只能归档成功且已释放资源的运行")
        if run["identity"].get("token_digest") or run["identity"].get("session_id"):
            raise ValueError("天气归档不能携带登录会话")
        for key in ("input_ref", "result_ref"):
            if contents[run[key]]["run_id"] != run["id"]:
                raise ValueError("运行输入输出缺失或串链")
        if index["release_snapshots"][run["release_snapshot_id"]]["run_id"] != run["id"]:
            raise ValueError("运行冻结快照缺失")
    for name, rows in tables.items():
        for row in rows:
            if "run_id" in row and row["run_id"] not in runs:
                raise ValueError(f"{name} 引用了归档之外的运行")
    for name in ("run_steps", "attempts", "tool_calls"):
        for row in tables[name]:
            for key in ("input_ref", "output_ref", "result_ref"):
                if row.get(key) and contents[row[key]]["run_id"] != row["run_id"]:
                    raise ValueError("步骤、调用或工具内容引用不完整")
    for row in tables["usage_records"]:
        if index["attempts"][row["attempt_id"]]["run_id"] != row["run_id"]:
            raise ValueError("模型用量与调用不匹配")
    for row in tables["resource_grants"]:
        if row["environments"] != ["dev"]:
            raise ValueError("初始化天气授权只能开放开发环境")
        if row["resource_type"] == "run" and (
            row["resource_id"] not in runs or row["allowed_actions"] != ["data:read_sensitive"]
        ):
            raise ValueError("天气原文查看必须限定成功记录")
    if any(row["status"] != "RELEASED" for row in tables["admissions"]):
        raise ValueError("不能恢复运行中的配额占用")
    for question, run_id in cases.items():
        run = runs[run_id]
        if contents[run["input_ref"]]["payload"] != {"request": question}:
            raise ValueError("天气测试输入与清单不匹配")
        result = BusinessResult.model_validate(contents[run["result_ref"]]["payload"])
        calls = [r for r in tables["tool_calls"] if r["run_id"] == run_id]
        if len(calls) != 1 or calls[0]["state"] != "SUCCEEDED":
            raise ValueError("天气测试必须包含一次成功的真实天气调用")
        step = index["run_steps"][calls[0]["step_id"]]
        weather = contents[step["output_ref"]]["payload"]["data"]
        city, day = ("南京", "今天") if question.startswith("今天") else ("北京", "明天")
        if contents[step["input_ref"]]["payload"] != {"city": city, "day": day}:
            raise ValueError("天气工具默认参数或指定参数未通过验证")
        expected_date = datetime.fromisoformat(weather["retrieved_at"]).date()
        if day == "明天":
            expected_date += timedelta(days=1)
        if (
            result.business_status != "COMPLETED"
            or result.data["city"] != weather["city"]
            or result.data["city"] != city
            or result.data["date"] != weather["date"]
            or weather["date"] != expected_date.isoformat()
            or result.data["source"] != weather["source"]
            or weather["source"] != "Open-Meteo"
            or weather["timezone"] != "Asia/Shanghai"
        ):
            raise ValueError("天气结果与工具日期、城市或来源不一致")
        if not result.evidence_refs:
            raise ValueError("最终天气回答缺少对应工具证据")
        for ref in result.evidence_refs:
            evidence = index["evidence_refs"].get(ref.evidence_id)
            if (
                ref.evidence_id not in calls[0]["evidence_ids"]
                or evidence is None
                or evidence["source_id"] != calls[0]["id"]
                or ref.scope.channel_id != channel
                or ref.scope.environment != "dev"
                or ref.source_version != evidence["source_version"]
            ):
                raise ValueError("最终天气回答缺少对应工具证据")
        loaded = [
            item
            for row in tables["run_contents"]
            if row["run_id"] == run_id
            for item in row["payload"].get("skills", {}).get("loaded", [])
        ]
        if not loaded:
            raise ValueError("成功天气测试未加载 Skill 正文")
    objects = {o["id"]: o for o in seed["objects"]}
    if objects.keys() != index["artifacts"].keys():
        raise ValueError("技能对象清单不完整")
    for ident, item in objects.items():
        artifact = index["artifacts"][ident]
        for key in (
            "channel_id",
            "environment",
            "object_key",
            "content_type",
            "size_bytes",
            "sha256",
        ):
            if item[key] != artifact[key]:
                raise ValueError("技能对象与数据库元数据不匹配")
        with zipfile.ZipFile(io.BytesIO(object_bytes(item))) as package:
            for file in tables["skill_files"]:
                data = package.read(file["relative_path"])
                if (
                    len(data) != file["size_bytes"]
                    or hashlib.sha256(data).hexdigest() != file["sha256"]
                ):
                    raise ValueError("Skill 文件内容与索引不匹配")


def load_weather(base: dict[str, Any]) -> dict[str, Any]:
    seed = json.loads(WEATHER_SEED.read_text(encoding="utf-8"))
    validate_weather(seed, base)
    return seed
