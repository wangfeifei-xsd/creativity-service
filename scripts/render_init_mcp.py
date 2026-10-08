"""离线校验 MCP 基本配置与凭据原文，不继承运行和连接验证状态。"""

from typing import Any
from urllib.parse import urlsplit

from creativity_service.modules.mcp.schemas import (
    McpAuthentication,
    McpAuthenticationInput,
    McpCreate,
)


def validate_initial_mcp(tables: dict[str, Any], tenants: dict[str, Any], admin_id: str) -> None:
    """凭据须与连接同渠道、同环境、同修订，恢复后重新进行握手与发现。"""
    if any(row["purpose"] not in {"model", "mcp"} for row in tables["credentials"]):
        raise ValueError("初始化凭据只能用于模型或 MCP")
    credentials = {row["id"]: row for row in tables["credentials"] if row["purpose"] == "mcp"}
    connections = tables["mcp_connections"]
    if {row["credential_ref"] for row in connections} != credentials.keys():
        raise ValueError("初始 MCP 连接必须关联完整的凭据，不能带孤立凭据")
    for row in connections:
        McpCreate.model_validate({key: row[key] for key in McpCreate.model_fields})
        auth = McpAuthentication.model_validate(row["authentication"])
        credential = credentials[row["credential_ref"]]
        McpAuthenticationInput.model_validate(
            {
                "revision": row["revision"],
                "app_id": auth.app_id,
                "token_endpoint": auth.token_endpoint,
                "app_secret": credential["secret_value"],
            }
        )
        if (
            row["channel_id"] not in tenants
            or row["environment"] != "dev"
            or (row["channel_id"], row["environment"])
            != (credential["channel_id"], credential["environment"])
            or row["credential_revision"] != credential["revision"]
            or credential["state"] != "ACTIVE"
            or not credential["secret_value"]
            or credential["key_version"] is not None
            or credential["ciphertext"] is not None
        ):
            raise ValueError("初始 MCP 凭据归属、修订或原文不合法，不能包含密文及主密钥版本")
        if row["transport"] != "streamable_http" or auth.mode != "client_credentials":
            raise ValueError("初始 MCP 使用 Streamable HTTP 与服务间鉴权")
        for endpoint in (row["endpoint"], auth.token_endpoint):
            url = urlsplit(endpoint)
            if (
                url.scheme not in {"http", "https"}
                or not url.hostname
                or url.username
                or url.password
                or url.query
                or url.fragment
            ):
                raise ValueError("初始 MCP 地址必须为不含认证信息的 HTTP 或 HTTPS 地址")
        initial_state = {
            "status": "DISABLED",
            "health_status": "UNKNOWN",
            "tested_revision": None,
            "discovered_revision": None,
            "failure_count": 0,
            "last_check_at": None,
            "auth_failed": False,
            "health_actor_id": admin_id,
        }
        if any(row[key] != value for key, value in initial_state.items()):
            raise ValueError("初始 MCP 只能归档配置与鉴权，须重新测试与启用")
