"""仅解释变量名及有限格式，输入正文不会被再次解析为模板。"""

import json
import math
import re
from collections.abc import Mapping
from typing import Any

from creativity_service.core.context import AuthContext
from creativity_service.core.primitives import ServiceError, canonical_json
from creativity_service.modules.prompts.schemas import (
    PromptContent,
    PromptRenderView,
    PromptRuntimeInput,
    PromptVariable,
    RenderedSection,
)

NAME = re.compile(r"^[A-Za-z][A-Za-z0-9_]{0,127}$")
TOKEN = re.compile(r"\{\{\s*([A-Za-z][A-Za-z0-9_]*)\s*(?:\|\s*(json|upper|lower)\s*)?\}\}")
PLATFORM_NAMES = {
    "channel_id",
    "environment",
    "principal_id",
    "actor_id",
    "subject_id",
}
LABELS = {
    "system": "系统指令",
    "output": "输出要求",
    "input": "调用输入",
    "tool": "工具结果",
    "memory": "记忆",
    "platform": "平台上下文",
}


class PromptFieldError(ServiceError):
    def __init__(self, path: list[str | int], label: str, reason: str) -> None:
        message = f"{label}：{reason}"
        super().__init__("PROMPT_VARIABLE_INVALID", message, 422)
        self.fields = [{"path": path, "message": message}]


def invalid(variable: PromptVariable, reason: str, *path: str | int) -> None:
    raise PromptFieldError(list(path) or ["input", variable.name], variable.display_name, reason)


def check_value(variable: PromptVariable, value: Any, *path: str | int) -> None:
    valid = {
        "string": type(value) is str,
        "integer": type(value) is int,
        "number": type(value) is int or (type(value) is float and math.isfinite(value)),
        "boolean": type(value) is bool,
        "object": type(value) is dict,
        "array": type(value) is list,
    }[variable.type]
    if not valid:
        invalid(variable, "类型不符合变量定义", *path)
    try:
        size = len(value) if isinstance(value, str) else len(canonical_json(value).decode("utf-8"))
    except (ValueError, TypeError, OverflowError):
        invalid(variable, "必须是有限的 JSON 值", *path)
        return
    if size > variable.max_length:
        invalid(variable, f"长度不能超过 {variable.max_length} 个字符", *path)


def templates(content: PromptContent) -> list[tuple[str, str, list[str | int]]]:
    result: list[tuple[str, str, list[str | int]]] = [
        ("system", content.instruction_blocks.system, ["instruction_blocks", "system"])
    ]
    for i, message in enumerate(content.message_templates):
        result.append((message.source, message.template, ["message_templates", i, "template"]))
    result.append(
        (
            "output",
            content.instruction_blocks.output_requirements,
            ["instruction_blocks", "output_requirements"],
        )
    )
    return result


def validate_content(content: PromptContent) -> None:
    variables: dict[str, PromptVariable] = {}
    for index, variable in enumerate(content.variables):
        path: list[str | int] = ["variables", index]
        if not NAME.fullmatch(variable.name) or variable.name in variables:
            invalid(variable, "模板引用名格式不正确或重复", *path, "name")
        if not re.search(r"[\u4e00-\u9fff]", variable.display_name):
            invalid(variable, "请填写中文显示名称", *path, "display_name")
        if variable.name in PLATFORM_NAMES and variable.source != "platform":
            invalid(variable, "平台保留变量只能由平台上下文注入", *path, "source")
        if variable.source == "platform":
            if variable.name not in PLATFORM_NAMES or variable.type != "string":
                invalid(variable, "未登记的平台字段或字段类型不正确", *path, "source")
            if variable.default is not None:
                invalid(variable, "平台变量不能设置默认值", *path, "default")
        if variable.default is not None:
            check_value(variable, variable.default, *path, "default")
        variables[variable.name] = variable
    total = 0
    for source, template, path in templates(content):
        total += len(template)
        remainder = TOKEN.sub("", template)
        if any(marker in remainder for marker in ("{{", "}}", "{%", "%}", "{#", "#}")):
            raise PromptFieldError(path, LABELS[source], "只允许变量替换及 json、upper、lower 格式")
        for match in TOKEN.finditer(template):
            name, formatter = match.groups()
            declared = variables.get(name)
            if declared is None:
                raise PromptFieldError(path, LABELS[source], f"变量 {name} 尚未声明")
            expected = "platform" if source in {"system", "output"} else source
            if declared.source != expected:
                invalid(declared, f"不能放入{LABELS[source]}分区", *path)
            if formatter in {"upper", "lower"} and declared.type != "string":
                invalid(declared, "大小写格式仅用于字符串", *path)
    if total > 200000:
        raise PromptFieldError(["instruction_blocks"], "模板", "总长度不能超过 200000 个字符")


def platform_values(context: AuthContext) -> dict[str, Any]:
    return {
        "channel_id": context.scope.channel_id,
        "environment": context.scope.environment,
        "principal_id": context.principal_id,
        "actor_id": context.actor_id,
        "subject_id": context.scope.subject_id,
    }


def bind_variables(
    content: PromptContent, context: AuthContext, inputs: PromptRuntimeInput
) -> dict[str, Any]:
    validate_content(content)
    sources: dict[str, Mapping[str, Any]] = {
        "input": inputs.input,
        "tool": inputs.tool,
        "memory": inputs.memory,
        "platform": platform_values(context),
    }
    variables = {v.name: v for v in content.variables}
    for source in ("input", "tool", "memory"):
        for name in sources[source]:
            declared = variables.get(name)
            if declared is None:
                raise PromptFieldError([source, name], "变量", f"{name} 尚未声明")
            if declared.source != source:
                invalid(declared, "输入来源与变量声明不符", source, name)
    result: dict[str, Any] = {}
    for variable in content.variables:
        value = sources[variable.source].get(variable.name, variable.default)
        if value is None:
            if variable.required:
                invalid(variable, "请提供必填值", variable.source, variable.name)
            result[variable.name] = ""
        else:
            check_value(variable, value, variable.source, variable.name)
            result[variable.name] = value
    return result


def render(
    content: PromptContent,
    context: AuthContext,
    inputs: PromptRuntimeInput,
    *,
    reveal_sensitive: bool = False,
    max_preview_chars: int | None = None,
    context_limit: int | None = None,
) -> PromptRenderView:
    values = bind_variables(content, context, inputs)
    variables = {v.name: v for v in content.variables}
    masked = False
    sections = []
    full_bytes = 0
    full_characters = 0
    formatted: dict[tuple[str, str | None], str] = {}
    preview_remaining = max_preview_chars
    for source, template, _path in templates(content):
        if not template:
            continue

        def substitute(match: re.Match[str], *, mask: bool) -> str:
            nonlocal masked
            name, formatter = match.groups()
            if mask and variables[name].sensitivity in {"sensitive", "secret"}:
                masked = True
                return "••••••"
            key = (name, formatter)
            if key not in formatted:
                value = values[name]
                if formatter == "json" or not isinstance(value, str):
                    text = json.dumps(
                        value, ensure_ascii=False, allow_nan=False, separators=(",", ":")
                    )
                elif formatter == "upper":
                    text = value.upper()
                elif formatter == "lower":
                    text = value.lower()
                else:
                    text = value
                formatted[key] = text
            return formatted[key]

        # 先计算替换后的大小，避免短模板通过重复大变量产生无界展开。
        expanded = len(template)
        for match in TOKEN.finditer(template):
            expanded += len(substitute(match, mask=False)) - len(match.group())
        full_characters += expanded
        if full_characters > 1_000_000:
            raise ServiceError(
                "CONTEXT_LIMIT_EXCEEDED",
                "渲染内容超过一百万字符，请压缩调用输入、工具结果或记忆",
                422,
            )

        complete = TOKEN.sub(lambda match: substitute(match, mask=False), template)
        full_bytes += len(complete.encode("utf-8"))
        displayed = TOKEN.sub(lambda match: substitute(match, mask=not reveal_sensitive), template)
        original = len(displayed)
        if preview_remaining is not None:
            displayed = displayed[:preview_remaining]
            preview_remaining = max(0, preview_remaining - len(displayed))
        sections.append(
            RenderedSection(
                source=source,
                label=LABELS[source],
                text=displayed,
                original_characters=original,
                truncated_characters=original - len(displayed),
            )
        )
    estimate = math.ceil(full_bytes / 3)
    if context_limit is not None and estimate > context_limit:
        raise ServiceError(
            "CONTEXT_LIMIT_EXCEEDED", "预计上下文超限，请压缩调用输入、工具结果或记忆", 422
        )
    return PromptRenderView(
        sections=sections,
        estimated_tokens=estimate,
        context_limit=context_limit,
        estimated_remaining_tokens=None
        if context_limit is None
        else max(0, context_limit - estimate),
        masked=masked,
    )
