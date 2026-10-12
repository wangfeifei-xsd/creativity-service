"""重新绑定的契约差异仅包含工具定义，不包含连接凭据或业务调用数据。"""

import json
from collections.abc import Iterator
from itertools import islice
from typing import Any

from creativity_service.core.primitives import ServiceError, canonical_json
from creativity_service.integrations.tools import EFFECT_LABELS
from creativity_service.modules.tools.schemas import ToolDefinition

_MISSING = object()
_MAX_DIFFERENCES = 50
_MAX_VALUE_LENGTH = 2000


def difference_fields(before: dict[str, Any], after: dict[str, Any]) -> list[dict[str, Any]]:
    """逐字段比较，保留缺失与空值的区别，并限制错误响应体积。"""

    def display(value: Any) -> str:
        if value is _MISSING:
            return "未声明"
        encoded = json.dumps(value, ensure_ascii=False, sort_keys=True)
        if len(encoded) > _MAX_VALUE_LENGTH:
            return encoded[:_MAX_VALUE_LENGTH] + "…（内容过长，已截断）"
        return encoded

    def walk(old: Any, new: Any, path: list[str]) -> Iterator[dict[str, Any]]:
        if isinstance(old, dict) and isinstance(new, dict):
            for key in sorted(old.keys() | new.keys()):
                yield from walk(old.get(key, _MISSING), new.get(key, _MISSING), [*path, key])
        elif old is _MISSING or new is _MISSING or canonical_json(old) != canonical_json(new):
            yield {"path": path, "message": f"原值：{display(old)}\n新值：{display(new)}"}

    fields = list(islice(walk(before, after, []), _MAX_DIFFERENCES + 1))
    if len(fields) > _MAX_DIFFERENCES:
        fields[-1] = {
            "path": [],
            "message": "差异超过 50 项，仅显示前 50 项，请在契约详情中核对完整定义。",
        }
    return fields


class ToolRebindError(ServiceError):
    def __init__(self, previous: ToolDefinition, current: ToolDefinition) -> None:
        super().__init__(
            "MCP_TOOL_CHANGED", "无法重新绑定：本地工具与本次发现的工具契约不一致", 409
        )
        self.previous = previous
        self.current = current
        self.fields = difference_fields(
            {
                "input_schema": previous.input_schema,
                "effect_type": EFFECT_LABELS[previous.effect_type],
                "implementation_version": previous.binding.implementation_version,
            },
            {
                "input_schema": current.input_schema,
                "effect_type": EFFECT_LABELS[current.effect_type],
                "implementation_version": current.binding.implementation_version,
            },
        )
