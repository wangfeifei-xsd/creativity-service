"""文本交换格式显式保存分区和变量；字符长度避免正文伪造分区边界。"""

import json
import re

from creativity_service.core.primitives import ServiceError
from creativity_service.modules.prompts.rendering import LABELS, templates
from creativity_service.modules.prompts.schemas import PromptContent, PromptPortable

HEADER = "提示词文本 v1\n"


def export_text(content: PromptContent) -> str:
    metadata = {
        "variables": [v.model_dump(mode="json") for v in content.variables],
        "change_note": content.change_note,
    }
    result = HEADER + json.dumps(metadata, ensure_ascii=False, separators=(",", ":")) + "\n"
    for source, template, _ in templates(content):
        result += f"【{LABELS[source]}】{len(template)} 字符\n{template}\n"
    return result


def import_text(data: str) -> PromptContent:
    if not data.startswith(HEADER):
        return PromptContent.model_validate({"instruction_blocks": {"system": data}})
    metadata_line, separator, rest = data[len(HEADER) :].partition("\n")
    try:
        metadata = json.loads(metadata_line)
        if (
            not separator
            or not isinstance(metadata, dict)
            or set(metadata) != {"variables", "change_note"}
        ):
            raise ValueError("文本元数据不完整")
        blocks: dict[str, str] = {}
        messages: list[dict[str, str]] = []
        while rest:
            header = re.match(r"【([^】]+)】([0-9]{1,7}) 字符\n", rest)
            if header is None:
                raise ValueError("分区格式不正确")
            label, size = header.groups()
            source = next((key for key, value in LABELS.items() if value == label), None)
            if source is None:
                raise ValueError("未知分区")
            rest = rest[header.end() :]
            length = int(size)
            if len(rest) < length + 1 or rest[length] != "\n":
                raise ValueError("分区长度不匹配")
            text, rest = rest[:length], rest[length + 1 :]
            if source in {"system", "output"}:
                field = "system" if source == "system" else "output_requirements"
                if field in blocks:
                    raise ValueError("分区重复")
                blocks[field] = text
            else:
                if len(messages) >= 100:
                    raise ValueError("消息分区过多")
                messages.append({"source": source, "template": text})
        return PromptPortable.model_validate(
            {"content": {**metadata, "instruction_blocks": blocks, "message_templates": messages}}
        ).content
    except (ValueError, TypeError) as exc:
        raise ServiceError("PROMPT_IMPORT_INVALID", "文本分区或变量定义格式不正确", 422) from exc
