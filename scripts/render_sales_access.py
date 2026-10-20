"""校验销售推荐的开发接入闭环，只归档当前渠道调用身份与凭据摘要。"""

from datetime import datetime
from typing import Any

ACCESS_TABLES = frozenset("service_clients channel_keys key_identity_index".split())
ACCESS_SCOPES = frozenset(
    "run:create run:read run:content conversation:read conversation:write "
    "content:delete data:read_sensitive".split()
)


def validate_sales_access(seed: dict[str, Any], index: dict[str, Any]) -> None:
    """接入身份和有效期必须闭合，不接受明文密钥或运行历史。"""
    tables, source = seed["tables"], seed["source"]
    if any(len(tables[name]) != 1 for name in ACCESS_TABLES):
        raise ValueError("销售推荐须保留一套完整的当前开发接入配置")
    client = tables["service_clients"][0]
    key = tables["channel_keys"][0]
    lookup = tables["key_identity_index"][0]
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
    if not key["expires_at"] or datetime.fromisoformat(
        source["captured_at"]
    ) >= datetime.fromisoformat(key["expires_at"]):
        raise ValueError("销售推荐渠道凭据在采集时必须处于明确的有效期内")
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
