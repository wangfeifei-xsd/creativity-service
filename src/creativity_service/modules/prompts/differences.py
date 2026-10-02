"""以中文名称展示变量兼容性与输出要求差异。"""

from creativity_service.modules.prompts.schemas import PromptContent, PromptDifference


def compare_content(before: PromptContent, after: PromptContent) -> list[PromptDifference]:
    changes = []
    previous, current = ({v.name: v for v in c.variables} for c in (before, after))
    labels = {
        "type": "类型",
        "required": "必填",
        "default": "默认值",
        "max_length": "长度上限",
        "source": "来源",
        "sensitivity": "敏感级别",
        "display_name": "显示名称",
    }
    for name in sorted(previous.keys() | current.keys()):
        left, right = previous.get(name), current.get(name)
        label = right.display_name if right else previous[name].display_name
        if left is None or right is None:
            changes.append(
                PromptDifference(
                    field=f"variables.{name}",
                    label=label,
                    before=left.model_dump(mode="json") if left else None,
                    after=right.model_dump(mode="json") if right else None,
                    breaking=right is None or (right.required and right.default is None),
                )
            )
            continue
        for field, field_label in labels.items():
            old, new = getattr(left, field), getattr(right, field)
            if old != new:
                breaking = (
                    field in {"type", "source"}
                    or (field == "required" and new is True and right.default is None)
                    or (field == "max_length" and new < old)
                )
                changes.append(
                    PromptDifference(
                        field=f"variables.{name}.{field}",
                        label=f"{label} · {field_label}",
                        before=old,
                        after=new,
                        breaking=breaking,
                    )
                )
    for field, label, old, new, breaking in (
        (
            "instruction_blocks.system",
            "系统指令",
            before.instruction_blocks.system,
            after.instruction_blocks.system,
            False,
        ),
        (
            "instruction_blocks.output_requirements",
            "输出要求",
            before.instruction_blocks.output_requirements,
            after.instruction_blocks.output_requirements,
            True,
        ),
        (
            "message_templates",
            "消息模板",
            [m.model_dump(mode="json") for m in before.message_templates],
            [m.model_dump(mode="json") for m in after.message_templates],
            False,
        ),
    ):
        if old != new:
            changes.append(
                PromptDifference(field=field, label=label, before=old, after=new, breaking=breaking)
            )
    return changes
