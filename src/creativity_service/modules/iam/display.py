"""授权与审计页面的名称投影；只能用于显示，不能替代资源授权和执行复核。"""

from typing import Any

from sqlalchemy.ext.asyncio import AsyncConnection

from creativity_service.core.auth.types import ResourceStateReader
from creativity_service.core.context import AuthContext
from creativity_service.core.database import Repository


def all_resources_name(kind: str) -> str:
    names = {
        "evaluation": "评测资源",
        "model": "模型",
        "prompt": "提示词",
        "tool": "工具",
        "mcp_connection": "MCP 连接",
        "agent": "智能体",
        "skill": "技能",
        "conversation": "会话",
        "memory": "记忆",
        "run": "运行记录",
        "model_route": "模型路由",
        "model_connection": "模型连接",
    }
    return f"全部{names[kind]}" if kind in names else "该类全部资源"


async def resource_names(
    connection: AsyncConnection,
    reader: ResourceStateReader | None,
    context: AuthContext,
    references: list[tuple[str, str]],
) -> dict[tuple[str, str], str]:
    from creativity_service.storage import metadata

    tables: dict[str, list[str]] = {}
    current: Any = reader
    while current is not None:
        for kind, names in getattr(current, "display_tables", {}).items():
            tables.setdefault(kind, []).extend(names)
        current = getattr(current, "previous", getattr(current, "fallback", None))
    result: dict[tuple[str, str], str] = {}
    requested = {(kind, identifier) for kind, identifier in references}
    connection_ids: set[str] = set()
    loaded: dict[str, dict[str, dict[str, Any]]] = {}
    for name in {name for kind, _ in requested for name in tables.get(kind, [])}:
        identifiers = {identifier for kind, identifier in requested if name in tables.get(kind, [])}
        rows = await Repository(metadata.tables[name], context.scope).get_many(
            connection, identifiers
        )
        loaded[name] = rows
        for loaded_row in rows.values():
            if loaded_row.get("connection_id"):
                connection_ids.add(loaded_row["connection_id"])
            if name == "resource_versions" and loaded_row["resource_type"] == "model":
                if loaded_row["content"].get("connection_id"):
                    connection_ids.add(loaded_row["content"]["connection_id"])
            if name == "resource_versions" and loaded_row["resource_type"] == "model_connection":
                connection_ids.add(loaded_row["resource_id"])
    connections = (
        await Repository(metadata.tables["model_connections"], context.scope).get_many(
            connection, connection_ids
        )
        if connection_ids
        else {}
    )
    for kind, identifier in requested:
        for name in dict.fromkeys(tables.get(kind, [])):
            row = loaded[name].get(identifier)
            if row is None:
                continue
            if name in {"models", "resource_versions"}:
                connection_id = row.get("connection_id")
                if name == "resource_versions":
                    if row["resource_type"] == "model":
                        connection_id = row["content"].get("connection_id")
                    elif row["resource_type"] == "model_connection":
                        connection_id = row["resource_id"]
                    elif row["resource_type"] == "model_route" and any(
                        m["scope"]["environment"] != context.scope.environment
                        for m in row["content"].get("models", [])
                    ):
                        continue
                if connection_id and connection_id not in connections:
                    continue
            value = (
                row.get("name")
                or row.get("title")
                or row.get("display_name")
                or row.get("agent_name")
                or row.get("version_label")
            )
            if value:
                result[(kind, identifier)] = str(value)
                break
        if kind not in tables and reader is not None:
            resource = await reader.read_current(context, kind, identifier)
            if resource and resource.scope == context.scope:
                result[(kind, identifier)] = resource.name
    return result
