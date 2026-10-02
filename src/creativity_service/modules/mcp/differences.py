"""发现只生成差异，绝不修改既有本地版本。"""

from creativity_service.modules.mcp.schemas import McpDiff, McpDifference, McpDiscovery


def differences(current: McpDiscovery, previous: McpDiscovery | None) -> McpDiff:
    before = {t.name: t for t in previous.tools} if previous else {}
    after = {t.name: t for t in current.tools}
    result = []
    labels = {"added": "新增", "removed": "已删除", "schema": "参数变更", "description": "描述变更"}
    for name in sorted(before.keys() | after.keys()):
        old, new = before.get(name), after.get(name)
        changes = []
        if old is None:
            changes.append("added")
        elif new is None:
            changes.append("removed")
        else:
            if old.schema_hash != new.schema_hash:
                changes.append("schema")
            if (old.description, old.title) != (new.description, new.title):
                changes.append("description")
        if changes:
            tool = new or old
            assert tool is not None
            result.append(
                McpDifference(
                    remote_tool_name=name,
                    name=tool.title,
                    changes=changes,
                    labels=[labels[c] for c in changes],
                    breaking=bool({"removed", "schema"} & set(changes)),
                )
            )
    return McpDiff(
        discovery_id=current.discovery_id,
        previous_discovery_id=previous.discovery_id if previous else None,
        items=result,
    )
