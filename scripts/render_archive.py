"""校验初始化配置的通用依赖与技能对象，不读取部署秘密或当前数据库。"""

import base64
import hashlib
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import Date, LargeBinary, Numeric, Table

from creativity_service.core.database.types import UTCDateTime
from creativity_service.modules.models.policy import configuration_digest, require_capabilities

OBJECT_MARKER = "-- creativity-object: "


def typed_archive_values(table: Table, source: dict[str, Any]) -> dict[str, Any]:
    """还原冻结时间、精确金额和密文字节，保持配置时间。"""
    row = dict(source)
    if table.name == "skill_files" and "package_url" in table.c:
        # 历史归档仍引用原产物，新增 URL 字段显式为空，不改动冻结包及其摘要。
        row.setdefault("package_url", None)
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


def object_bytes(item: dict[str, Any]) -> bytes:
    """对象内容与清单必须一致，避免恢复损坏的技能包。"""
    data = base64.b64decode(item["base64"], validate=True)
    if len(data) != item["size_bytes"] or hashlib.sha256(data).hexdigest() != item["sha256"]:
        raise ValueError("技能对象大小或摘要不匹配")
    return data


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
