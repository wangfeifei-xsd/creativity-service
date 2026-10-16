"""发现及重新绑定的差异说明，绝不修改既有本地版本。"""

from typing import Any

from creativity_service.core.primitives import ServiceError
from creativity_service.modules.mcp.schemas import McpDiff, McpDifference, McpDiscovery, RemoteTool
from creativity_service.modules.tools.rebinding import ToolRebindError, difference_fields


def remote_contract(tool: RemoteTool) -> dict[str, Any]:
    return {
        "remote_input_schema": tool.input_schema,
        "output_schema": tool.output_schema,
        "annotations": {k: v for k, v in tool.annotations.items() if k != "title"},
        "purpose": tool.purpose,
    }


class McpDiscoveryStaleError(ServiceError):
    def __init__(self, selected: RemoteTool, latest: RemoteTool | None) -> None:
        super().__init__(
            "MCP_DISCOVERY_STALE", "所选快照的工具契约已过期，请查看最新发现后重试", 409
        )
        self.fields = (
            difference_fields(remote_contract(selected), remote_contract(latest))
            if latest
            else [{"path": ["remote_tool"], "message": "原值：存在\n新值：最新发现中已移除"}]
        ) or [{"path": [], "message": "工具定义字段一致，但快照契约摘要不同，请重新发现工具。"}]


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
    remote_fields = difference_fields(remote_contract(previous), remote_contract(current))
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
