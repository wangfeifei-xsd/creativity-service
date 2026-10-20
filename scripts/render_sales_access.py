"""校验销售推荐的开发接入闭环，只归档当前凭据摘要、密文及复核配置。"""

from datetime import datetime
from typing import Any

from mcp.types import Tool

from creativity_service.modules.mcp.transport import remote_tool

ACCESS_TABLES = frozenset(
    "service_clients channel_keys key_identity_index delegation_keys "
    "credentials subject_review_bindings".split()
)
ACCESS_SCOPES = frozenset(
    "run:create run:read run:content conversation:read conversation:write "
    "data:read_sensitive".split()
)


def validate_sales_access(seed: dict[str, Any], index: dict[str, Any]) -> None:
    """接入身份、有效期和只读复核契约必须闭合，不接受明文或历史记录。"""
    tables, source = seed["tables"], seed["source"]
    if any(len(tables[name]) != 1 for name in ACCESS_TABLES):
        raise ValueError("销售推荐须保留一套完整的当前开发接入配置")
    client = tables["service_clients"][0]
    key = tables["channel_keys"][0]
    lookup = tables["key_identity_index"][0]
    delegation = tables["delegation_keys"][0]
    credential = tables["credentials"][0]
    binding = tables["subject_review_bindings"][0]
    if (
        client["id"] != source["client_id"]
        or client["status"] != "ACTIVE"
        or set(client["scopes"]) != ACCESS_SCOPES
        or key["client_id"] != client["id"]
        or key["status"] != "ACTIVE"
        or set(key["scopes"]) != ACCESS_SCOPES
        or key["last_used_at"] is not None
        or len(key["secret_digest"]) != 64
        or lookup["channel_id"] != "system"
        or lookup["target_channel_id"] != source["channel_id"]
        or lookup["key_id"] != key["id"]
        or len(lookup["key_lookup_digest"]) != 64
    ):
        raise ValueError("销售推荐服务、Key 摘要或系统凭据索引不一致")
    if (
        delegation["client_id"] != client["id"]
        or delegation["status"] != "ACTIVE"
        or delegation["algorithm"] != "HMAC-SHA256"
        or delegation["issuer"] != "playmate-backend"
        or delegation["audience"] != "creativity"
        or delegation["max_ttl_seconds"] != 300
        or delegation["clock_skew_seconds"] != 30
        or delegation["rotated_from"] is not None
        or delegation["key_reference"] != credential["id"]
        or credential["purpose"] != "delegation"
        or credential["state"] != "ACTIVE"
        or credential["secret_value"] is not None
        or not credential["key_version"]
        or not credential["ciphertext"]
    ):
        raise ValueError("销售推荐委托身份或密文凭据不完整，不能归档明文秘密")
    if (
        not key["expires_at"]
        or key["expires_at"] != delegation["expires_at"]
        or not delegation["not_before"]
        or not (
            datetime.fromisoformat(delegation["not_before"])
            <= datetime.fromisoformat(source["captured_at"])
            < datetime.fromisoformat(key["expires_at"])
        )
    ):
        raise ValueError("销售推荐接入凭据在采集时必须处于明确的有效期内")
    connection = index["mcp_connections"][source["mcp_connection_id"]]
    discovery = index["mcp_discoveries"].get(binding["discovery_id"], {})
    matches = [
        item
        for item in discovery.get("tool_definitions", [])
        if item["name"] == binding["remote_tool_name"]
    ]
    if (
        binding["client_id"] != client["id"]
        or binding["connection_id"] != connection["id"]
        or binding["enabled"] is not True
        or binding["timeout_seconds"] != 5
        or binding["connection_revision"] != connection["configuration_revision"]
        or discovery.get("connection_id") != connection["id"]
        or discovery.get("connection_revision") != connection["configuration_revision"]
        or discovery.get("credential_revision") != connection["credential_revision"]
        or len(matches) != 1
    ):
        raise ValueError("销售推荐主体复核绑定或发现配置不完整")
    remote = matches[0]
    contract = remote_tool(
        Tool.model_validate(
            {
                "name": remote["name"],
                "inputSchema": remote["input_schema"],
                "outputSchema": remote["output_schema"],
                "annotations": remote["annotations"],
                "_meta": {"creativity/purpose": remote["purpose"]},
            }
        )
    )
    if (
        remote["name"] != "playmate_ai_match.review_subject"
        or contract.purpose != "subject_review"
        or contract.annotations.get("readOnlyHint") is not True
        or contract.annotations.get("destructiveHint") is True
        or any(
            value != contract.schema_hash
            for value in (
                remote["schema_hash"],
                binding["schema_hash"],
                discovery["schema_hashes"].get(remote["name"]),
            )
        )
        or any(r["remote_tool_name"] == remote["name"] for r in tables["mcp_imports"])
    ):
        raise ValueError("销售推荐主体复核须使用只读身份工具，不能导入为业务工具")
    latest = max(
        (r for r in index["mcp_discoveries"].values() if r["connection_id"] == connection["id"]),
        key=lambda row: row["created_at"],
    )
    if latest["schema_hashes"].get(remote["name"]) != contract.schema_hash:
        raise ValueError("销售推荐主体复核与当前发现契约不一致")
    admin = next(iter(index["platform_accounts"]))
    if not any(
        row["grantee_type"] == "account"
        and row["grantee_id"] == admin
        and row["resource_type"] == "channel"
        and row["resource_id"] == source["channel_id"]
        and row["environments"] == ["dev"]
        and "data:read_sensitive" in row["allowed_actions"]
        for row in index["resource_grants"].values()
    ):
        raise ValueError("销售推荐缺少初始管理员已确认的开发环境敏感原文授权")
