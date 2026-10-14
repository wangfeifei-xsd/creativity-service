"""离线校验 GTP 连接、模型和已发布路由的配置归档，不读取数据库或部署主密钥。"""

import json
from pathlib import Path
from typing import Any

from creativity_service.core.context import Scope
from creativity_service.core.database import validate_row
from creativity_service.core.deletion import barrier_id
from creativity_service.core.primitives import digest
from creativity_service.modules.models.policy import attempt_order, validate_parameters
from creativity_service.modules.models.schemas import (
    ConnectionInput,
    FrozenModel,
    ModelInput,
    RouteVersionInput,
)
from creativity_service.storage import metadata
from scripts.render_weather_seed import typed_archive_values, validate_configuration_references

GPT_SEED = Path(__file__).resolve().parents[1] / "sql/gpt_data.json"
GPT_TABLES = frozenset(
    "credentials model_connections models model_routes resource_versions "
    "resource_references source_links release_mappings".split()
)
RESOURCE_TABLES = {
    "model_connection": "model_connections",
    "model": "models",
    "model_route": "model_routes",
}


def merge_gpt(base: dict[str, Any], additions: dict[str, Any]) -> dict[str, Any]:
    """追加独立模型配置，拒绝覆盖天气、订单协助及基础种子的既有记录。"""
    result = {name: {row["id"]: row for row in rows} for name, rows in base.items()}
    for name, rows in additions.items():
        current = result.setdefault(name, {})
        for row in rows:
            if row["id"] in current:
                raise ValueError(f"GTP 归档不能覆盖 {name}")
            current[row["id"]] = row
    return {name: sorted(rows.values(), key=lambda row: row["id"]) for name, rows in result.items()}


def validate_gpt(seed: dict[str, Any], base: dict[str, Any]) -> None:
    """检查密文、配置依赖、发布和能力证据，保留当前模型仍引用的旧连接版本。"""
    tables, source = seed["tables"], seed["source"]
    if set(tables) != GPT_TABLES or source["environment"] != "dev":
        raise ValueError("GTP 归档表清单或环境不正确")
    scope = Scope(channel_id=source["channel_id"], environment="dev")
    for name, rows in tables.items():
        if not rows or len({row["id"] for row in rows}) != len(rows):
            raise ValueError(f"{name} 归档为空或标识重复")
        for row in rows:
            if set(row) != set(metadata.tables[name].c.keys()):
                raise ValueError(f"{name} 字段与模型不一致")
            validate_row(metadata.tables[name], typed_archive_values(metadata.tables[name], row))
            if (
                row["channel_id"] != scope.channel_id
                or row.get("environment", "dev") != "dev"
                or row.get("subject_type") is not None
                or row.get("subject_id") is not None
                or row["is_deleted"]
            ):
                raise ValueError("GTP 归档只能包含同渠道开发环境的未删除配置")
    index = {
        name: {row["id"]: row for row in rows} for name, rows in merge_gpt(base, tables).items()
    }
    barrier = index["recovery_barriers"].get(barrier_id(scope), {})
    if (
        barrier.get("state") != "READY"
        or barrier.get("marker_digest") != digest([])
        or barrier.get("verified_at") is None
    ):
        raise ValueError("GTP 配置恢复屏障缺失或未核验")
    credentials = {row["id"]: row for row in tables["credentials"]}
    if {row["credential_ref"] for row in tables["model_connections"]} != credentials.keys():
        raise ValueError("GTP 连接的密文凭据缺失或孤立")
    for row in credentials.values():
        if (
            row["purpose"] != "model"
            or row["state"] != "ACTIVE"
            or row["secret_value"] is not None
            or not row["key_version"]
            or len(bytes.fromhex(row["ciphertext"])) < 29
        ):
            raise ValueError("GTP 凭据必须是启用的模型密文，不能包含明文")
    versions = {row["id"]: row for row in tables["resource_versions"]}
    for row in versions.values():
        kind = row["resource_type"]
        if (
            kind not in RESOURCE_TABLES
            or row["resource_id"] not in {r["id"] for r in tables[RESOURCE_TABLES[kind]]}
            or row["state"] != "PUBLISHED"
            or not set(row["dependencies"]) <= versions.keys()
        ):
            raise ValueError("GTP 版本归属、状态或依赖不完整")
        resolved = [
            {
                "version_id": dep,
                "content_digest": versions[dep]["content_digest"],
                "dependencies_digest": versions[dep]["dependencies_digest"],
            }
            for dep in row["dependencies"]
        ]
        # 路由采用当前资源配置摘要；模型和连接沿用冻结版本的递归依赖摘要。
        expected = (
            digest(sorted(set(row["dependencies"]))) if kind == "model_route" else digest(resolved)
        )
        if (
            row["content_digest"]
            != digest({"content": row["content"], "output_schema": row["output_schema"]})
            or row["dependencies_digest"] != expected
        ):
            raise ValueError("GTP 版本内容或依赖摘要不一致")
    for kind, schema in (("model_connection", ConnectionInput), ("model", ModelInput)):
        for row in tables[RESOURCE_TABLES[kind]]:
            version = versions.get(row["current_version_id"], {})
            body = schema.model_validate({key: row[key] for key in schema.model_fields})
            if (
                version.get("resource_type") != kind
                or version.get("resource_id") != row["id"]
                or row["status"] != "ACTIVE"
                or any(
                    version["content"].get(key) != value
                    for key, value in body.model_dump(exclude={"revision"}).items()
                )
            ):
                raise ValueError("GTP 当前模型或连接与冻结配置不一致")
    for row in tables["model_connections"]:
        provider = index["provider_catalog"].get(row["provider_id"], {})
        if row["protocol"] not in provider.get("protocols", []):
            raise ValueError("GTP 连接供应商或协议缺失")
    for row in tables["models"]:
        connection = index["model_connections"].get(row["connection_id"], {})
        if connection.get("channel_id") != scope.channel_id:
            raise ValueError("GTP 模型连接缺失或跨渠道")
        validate_parameters(connection["protocol"], row["parameter_allowlist"], row["parameters"])
    for row in tables["resource_references"]:
        if (
            row["source_version_id"] not in versions
            or row["target_version_id"] not in versions
            or row["target_version_id"] not in versions[row["source_version_id"]]["dependencies"]
            or row["target_resource_type"] != versions[row["target_version_id"]]["resource_type"]
        ):
            raise ValueError("GTP 版本引用不完整")
    routes = {row["id"] for row in tables["model_routes"]}
    if (
        source["route_id"] not in routes
        or {row["resource_id"] for row in tables["release_mappings"]} != routes
    ):
        raise ValueError("GTP 路由发布映射缺失")
    for row in tables["release_mappings"]:
        version = versions.get(row["version_id"], {})
        if (
            row["resource_type"] != "model_route"
            or version.get("resource_type") != "model_route"
            or version.get("resource_id") != row["resource_id"]
            or row["version_id"] != row["resource_id"]
        ):
            raise ValueError("GTP 路由发布映射不一致")
        content = version["content"]
        body = RouteVersionInput.model_validate(
            {key: value for key, value in content.items() if key in RouteVersionInput.model_fields}
        )
        candidates = [body.primary_model, *body.fallback_models]
        if (
            content["attempt_order"] != list(attempt_order(candidates, body.retry_policy))
            or [item["model_id"] for item in content["models"]] != candidates
        ):
            raise ValueError("GTP 路由候选或重试顺序不一致")
        for item in content["models"]:
            snapshot = FrozenModel.model_validate(item)
            model = index["models"].get(snapshot.model_id, {})
            connection = index["model_connections"].get(snapshot.connection_id, {})
            if (
                snapshot.scope != scope
                or snapshot.model_version_id != model.get("current_version_id")
                or snapshot.connection_id != model.get("connection_id")
                or snapshot.connection_version_id != connection.get("current_version_id")
                or snapshot.provider_credential_id != connection.get("credential_ref")
                or not {snapshot.model_version_id, snapshot.connection_version_id}
                <= set(version["dependencies"])
            ):
                raise ValueError("GTP 路由快照归属或模型依赖不一致")
    validate_configuration_references(index)


def load_gpt(base: dict[str, Any]) -> dict[str, Any]:
    seed = json.loads(GPT_SEED.read_text(encoding="utf-8"))
    validate_gpt(seed, base)
    return seed
