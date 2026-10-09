"""校验天气助手的冻结配置；不读取部署秘密或当前数据库。"""

import base64
import hashlib
import io
import json
import zipfile
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

from sqlalchemy import Date, LargeBinary, Numeric, Table

from creativity_service.core.context import Scope
from creativity_service.core.database import validate_row
from creativity_service.core.database.types import UTCDateTime
from creativity_service.core.deletion import barrier_id
from creativity_service.core.primitives import digest
from creativity_service.modules.models.policy import configuration_digest, require_capabilities
from creativity_service.storage import metadata

WEATHER_SEED = Path(__file__).resolve().parents[1] / "sql/weather_data.json"
OBJECT_MARKER = "-- creativity-object: "
WEATHER_TABLES = frozenset(
    "agents skills prompts model_routes tools mcp_connections channel_environments models "
    "prompt_samples resource_versions model_connections credentials release_mappings "
    "resource_references mcp_discoveries mcp_imports skill_files artifacts resource_grants "
    "source_links channel_memberships recovery_barriers".split()
)
# 同一模型和初始管理员沿用原标识；天气归档只补充已验证配置及开发环境。
OVERRIDE_TABLES = frozenset(
    "models model_connections credentials resource_versions resource_references source_links "
    "channel_memberships resource_grants".split()
)


def typed_archive_values(table: Table, source: dict[str, Any]) -> dict[str, Any]:
    """还原冻结时间、精确金额和密文字节，保持配置时间。"""
    row = dict(source)
    for column in table.c:
        value = row.get(column.name)
        if value is None:
            continue
        if isinstance(column.type, UTCDateTime):
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
    """校验配置范围、依赖、授权和技能对象，不归档测试与操作历史。"""
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
    # SQL 导入不会经过环境创建服务，必须显式带齐所有内容范围的恢复屏障。
    scopes = {
        barrier_id(scope): scope
        for name, rows in tables.items()
        if name != "recovery_barriers" and "environment" in metadata.tables[name].c
        for row in rows
        if row.get("environment")
        for scope in [Scope(**{key: row.get(key) for key in Scope.model_fields})]
    }
    barriers = index["recovery_barriers"]
    if barriers.keys() != scopes.keys():
        raise ValueError("初始化内容范围的恢复屏障缺失或多余")
    for identifier, scope in scopes.items():
        row = barriers[identifier]
        if (
            any(row[key] != value for key, value in scope.model_dump().items())
            or row["state"] != "READY"
            or row["marker_digest"] != digest([])
            or row["verified_at"] is None
        ):
            raise ValueError("初始化恢复屏障范围、状态或删除摘要不正确")
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
    validate_configuration_references(index)
    for row in tables["resource_grants"]:
        if row["environments"] != ["dev"] or row["resource_type"] == "run":
            raise ValueError("初始化天气授权只能开放开发环境配置，不能引用历史运行")
    admin = base["platform_accounts"][0]["id"]
    content_grants = [
        row
        for row in tables["resource_grants"]
        if row["resource_type"] in {"conversation", "memory"}
    ]
    if len(content_grants) != 2 or {row["resource_type"] for row in content_grants} != {
        "conversation",
        "memory",
    }:
        raise ValueError("初始管理员的会话与记忆原文授权缺失或重复")
    if any(
        row["grantee_type"] != "account"
        or row["grantee_id"] != admin
        or row["resource_id"] != "*"
        or row["allowed_actions"] != ["data:read_sensitive"]
        for row in content_grants
    ):
        raise ValueError("会话与记忆原文授权只能授予初始管理员及指定资源类型")
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


def validate_configuration_references(index: dict[str, dict[str, Any]]) -> None:
    """保留已验证能力摘要和配置依赖，拒绝运行快照及已删除历史的悬空引用。"""
    versions = index["resource_versions"]
    identifiers = {
        kind: set(index.get(table, {}))
        for kind, table in {
            "agent": "agents",
            "version": "resource_versions",
            "model": "models",
            "model_connection": "model_connections",
            "prompt": "prompts",
            "prompt_sample": "prompt_samples",
            "skill": "skills",
            "skill_file": "skill_files",
            "tool": "tools",
            "artifact": "artifacts",
        }.items()
    }
    for version in versions.values():
        if version["resource_type"] == "runtime":
            raise ValueError("初始化配置不能包含运行快照版本")
        identifiers.setdefault(version["resource_type"], set()).add(version["resource_id"])
    for link in index["source_links"].values():
        for kind, key in (("source_type", "source_id"), ("derived_type", "derived_id")):
            if link[key] not in identifiers.get(link[kind], set()):
                raise ValueError("初始化来源关联只能引用已归档配置")
    for model in index["models"].values():
        for capability in model["capabilities"].values():
            if "test_id" in capability:
                raise ValueError("模型能力摘要不能引用未归档的测试记录")
    for route in index["model_routes"].values():
        content = versions[route["id"]]["content"]
        for snapshot in content["models"]:
            model = index["models"][snapshot["model_id"]]
            connection = index["model_connections"][snapshot["connection_id"]]
            if snapshot["config_digest"] != configuration_digest(model, connection):
                raise ValueError("模型路由与当前连接配置不一致")
            require_capabilities(
                model["capabilities"], snapshot["config_digest"], content["required_capabilities"]
            )


def load_weather(base: dict[str, Any]) -> dict[str, Any]:
    seed = json.loads(WEATHER_SEED.read_text(encoding="utf-8"))
    validate_weather(seed, base)
    return seed
