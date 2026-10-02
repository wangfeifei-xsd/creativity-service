"""运行和 Agent 发布共用固定 MCP 绑定校验，不在事务中访问远端。"""

from typing import Any

from creativity_service.core.primitives import ServiceError
from creativity_service.modules.tools.schemas import ToolBinding


def require_current_binding(
    binding: ToolBinding,
    connection: dict[str, Any],
    imported: dict[str, Any],
    original: dict[str, Any],
    latest: dict[str, Any] | None,
) -> None:
    if (
        imported["contract_status"] != "CURRENT"
        or imported["connection_id"] != connection["id"]
        or binding.connection_id != connection["id"]
        or binding.implementation_version != imported["schema_hash"]
        or original["connection_id"] != connection["id"]
        or original["connection_revision"] != connection["configuration_revision"]
        or latest is None
        or latest["connection_revision"] != connection["configuration_revision"]
        or latest["schema_hashes"].get(imported["remote_tool_name"]) != imported["schema_hash"]
    ):
        raise ServiceError("MCP_TOOL_CHANGED", "连接或工具契约已变更，请导入新版本并重新授权", 409)
    if connection["status"] != "ENABLED":
        raise ServiceError("MCP_UNAVAILABLE", "连接未启用", 409)
    if connection["auth_failed"]:
        raise ServiceError("MCP_AUTH_FAILED", "连接凭据已失效", 403)
