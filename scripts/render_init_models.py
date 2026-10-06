"""离线校验预置模型、密文与版本依赖；不读取部署主密钥或继承能力证据。"""

from typing import Any

from creativity_service.core.primitives import ServiceError, digest
from creativity_service.modules.channels.repositories import management_scope_id
from creativity_service.modules.models.outbound import normalize_networks
from creativity_service.modules.models.policy import (
    configuration_digest,
    validate_endpoint,
    validate_parameters,
)
from creativity_service.modules.models.schemas import ConnectionInput, ModelInput

MODEL_SEED_TABLES = (
    "credentials",
    "model_connections",
    "models",
    "resource_references",
    "source_links",
)


def validate_initial_models(tables: dict[str, Any], tenants: dict[str, Any], admin_id: str) -> None:
    """配置只引用同渠道开发环境密文，并完整归档版本及来源关联。"""
    credentials = {row["id"]: row for row in tables["credentials"]}
    connections = {row["id"]: row for row in tables["model_connections"]}
    providers = {row["id"]: row for row in tables["provider_catalog"]}
    versions = {row["id"]: row for row in tables["resource_versions"]}
    expected_versions: dict[str, tuple[dict[str, Any], str, dict[str, Any], list[str]]] = {}
    for row in credentials.values():
        if (
            row["channel_id"] not in tenants
            or row["environment"] != "dev"
            or row["purpose"] != "model"
            or row["state"] != "ACTIVE"
            or not row["key_version"]
            or len(bytes.fromhex(row["ciphertext"])) < 29
        ):
            raise ValueError("初始模型凭据归属、密文或密钥版本不合法")
    if {row["credential_ref"] for row in connections.values()} != credentials.keys():
        raise ValueError("初始模型连接必须关联完整的密文凭据，不能带孤立凭据")
    for row in connections.values():
        credential = credentials[row["credential_ref"]]
        provider = providers.get(row["provider_id"])
        if (
            (row["channel_id"], row["environment"])
            != (credential["channel_id"], credential["environment"])
            or provider is None
            or row["protocol"] not in provider["protocols"]
            or row["health_status"] != "UNKNOWN"
            or row["health_reason"] is not None
            or row["health_checked_at"] is not None
        ):
            raise ValueError("初始模型连接归属或协议不一致，健康状态必须重新验证")
        body = ConnectionInput.model_validate({k: row[k] for k in ConnectionInput.model_fields})
        if validate_endpoint(body.protocol, body.endpoint) != body.endpoint:
            raise ValueError("初始模型连接地址未规范化")
        if normalize_networks(body.allowed_networks) != body.allowed_networks:
            raise ValueError("初始模型连接网络范围未规范化")
        content = body.model_dump(exclude={"revision"}) | {
            k: row[k]
            for k in (
                "current_version_id",
                "health_status",
                "health_reason",
                "health_checked_at",
                "validation_revision",
            )
        }
        expected_versions[row["current_version_id"]] = (row, "model_connection", content, [])
    for row in tables["models"]:
        connection = connections.get(row["connection_id"])
        if (
            connection is None
            or connection["channel_id"] != row["channel_id"]
            or row["capabilities"] != {}
            or row["verified_at"] is not None
        ):
            raise ValueError("初始模型连接必须同渠道，能力不能继承旧验证结果")
        body = ModelInput.model_validate({k: row[k] for k in ModelInput.model_fields})
        try:
            validate_parameters(connection["protocol"], body.parameter_allowlist, body.parameters)
        except ServiceError as exc:
            raise ValueError("初始模型参数不合法") from exc
        content = body.model_dump(exclude={"revision"}) | {
            "connection_version_id": connection["current_version_id"],
            "validation_revision": row["validation_revision"],
            "config_digest": configuration_digest(row, connection),
        }
        expected_versions[row["current_version_id"]] = (
            row,
            "model",
            content,
            [connection["current_version_id"]],
        )
    if {row["connection_id"] for row in tables["models"]} != connections.keys():
        raise ValueError("初始模型连接不能缺失或孤立")
    if {
        v["id"] for v in versions.values() if v["resource_type"] != "budget_policy"
    } != expected_versions.keys():
        raise ValueError("初始模型版本缺失或包含无关版本")
    references, links = {}, {}
    for version_id, (row, kind, content, dependencies) in expected_versions.items():
        version = versions[version_id]
        resolved = [
            {
                "version_id": dep,
                "content_digest": versions[dep]["content_digest"],
                "dependencies_digest": versions[dep]["dependencies_digest"],
            }
            for dep in dependencies
        ]
        expected = {
            "channel_id": row["channel_id"],
            "resource_type": kind,
            "resource_id": row["id"],
            "state": "PUBLISHED",
            "content": content,
            "output_schema": {},
            "content_digest": digest({"content": content, "output_schema": {}}),
            "dependencies": dependencies,
            "dependencies_digest": digest(resolved),
            "created_by": admin_id,
        }
        if any(version[key] != value for key, value in expected.items()):
            raise ValueError("初始模型冻结版本内容、归属或摘要不一致")
        for dep in dependencies:
            references[digest([version_id, dep])] = {
                "channel_id": row["channel_id"],
                "source_version_id": version_id,
                "target_version_id": dep,
                "target_resource_type": versions[dep]["resource_type"],
            }
        for source_type, source_id in [
            (kind, row["id"]),
            *[("version", dep) for dep in dependencies],
        ]:
            links[digest([version_id, source_type, source_id])] = {
                "channel_id": row["channel_id"],
                "environment": "dev",
                "data_scope_id": management_scope_id(row["channel_id"], "dev"),
                "subject_type": None,
                "subject_id": None,
                "source_type": source_type,
                "source_id": source_id,
                "derived_type": "version",
                "derived_id": version_id,
                "source_version": None,
            }
    for name, expected_rows in (("resource_references", references), ("source_links", links)):
        actual = {
            row["id"]: {k: v for k, v in row.items() if k not in {"id", "revision"}}
            for row in tables[name]
        }
        if actual != expected_rows:
            raise ValueError("初始模型依赖或来源关联不完整")
