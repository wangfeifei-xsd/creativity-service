"""发现只生成差异，绝不修改既有本地版本。"""

from creativity_service.modules.mcp.schemas import McpDiff, McpDifference, McpDiscovery, RemoteTool
from creativity_service.modules.tools.rebinding import ToolRebindError, difference_fields


def explain_rebind(
    error: ToolRebindError, previous: RemoteTool | None, current: RemoteTool
) -> None:
    """用实际绑定的历史快照解释摘要变化，不能用最近两次发现代替原绑定。"""
    if error.previous.binding.implementation_version == current.schema_hash:
        return
    if previous is None or previous.schema_hash != error.previous.binding.implementation_version:
        error.fields.append(
            {"path": [], "message": "原绑定的远端定义不可用，无法展开契约摘要差异。"}
        )
        return
    before = {
        "remote_input_schema": previous.input_schema,
        "output_schema": previous.output_schema,
        "annotations": {k: v for k, v in previous.annotations.items() if k != "title"},
        "purpose": previous.purpose,
    }
    after = {
        "remote_input_schema": current.input_schema,
        "output_schema": current.output_schema,
        "annotations": {k: v for k, v in current.annotations.items() if k != "title"},
        "purpose": current.purpose,
    }
    remote_fields = difference_fields(before, after)
    if not remote_fields:
        error.fields.append(
            {"path": [], "message": "远端定义字段一致，但契约摘要不同，请核对历史绑定数据。"}
        )
        return
    # 正常导入的本地入参只增加封闭对象约束，显示原远端入参差异即可；本地编辑另列。
    normal_input = error.previous.input_schema == {
        **previous.input_schema,
        "additionalProperties": False,
    }
    error.fields = [
        field
        for field in error.fields
        if field["path"] != ["implementation_version"]
        and not (normal_input and field["path"][:1] == ["input_schema"])
    ] + remote_fields


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
