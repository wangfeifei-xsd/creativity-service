"""通用执行入口与初始配置；历史场景入口只用于版本兼容。"""

from creativity_service.modules.agents.schemas import (
    AgentDefinition,
    AgentEdge,
    AgentStep,
    AgentTemplate,
    InputSource,
    WorkflowType,
)

GENERIC_ENTRYPOINTS: dict[str, tuple[WorkflowType, str]] = {
    "structured.v1": ("structured", "单步结构化任务"),
    "workflow.v1": ("template", "通用流程"),
    "tool_loop.v1": ("tool_loop", "受约束工具循环"),
    "stateful.v1": ("stateful", "有状态流程"),
}
LEGACY_ENTRYPOINTS: dict[str, tuple[WorkflowType, str]] = {
    "matching.v1": ("template", "智能匹配（旧版）"),
    "risk.v1": ("template", "风险评估（旧版）"),
    "analysis.v1": ("template", "数据分析（旧版）"),
}
ENTRYPOINTS = GENERIC_ENTRYPOINTS | LEGACY_ENTRYPOINTS


def templates() -> list[AgentTemplate]:
    return _templates(GENERIC_ENTRYPOINTS)


def legacy_templates() -> list[AgentTemplate]:
    """保持旧版定义内容与拓扑规则，不能静默替换已保存版本和快照。"""
    return _templates(LEGACY_ENTRYPOINTS)


def _templates(entries: dict[str, tuple[WorkflowType, str]]) -> list[AgentTemplate]:
    result = []
    for key, (kind, name) in entries.items():
        input_schema = {
            "type": "object",
            "properties": {"request": {"type": "string", "title": "业务诉求", "minLength": 1}},
            "required": ["request"],
            "additionalProperties": False,
        }
        output = {
            "type": "object",
            "properties": {
                "business_status": {
                    "type": "string",
                    "enum": [
                        "COMPLETED",
                        "NEEDS_INPUT",
                        "NO_MATCH",
                        "INSUFFICIENT_DATA",
                        "PARTIAL",
                    ],
                },
                "schema_version": {"type": "string", "const": "1.0"},
                "data": {"type": "object"},
                "warnings": {"type": "array", "items": {"type": "string"}},
                "evidence_refs": {"type": "array", "items": {"type": "object"}},
            },
            "required": ["business_status", "schema_version", "data", "warnings", "evidence_refs"],
            "additionalProperties": False,
        }
        definition = AgentDefinition(
            workflow_type=kind,
            entrypoint=key,
            input_schema=input_schema,
            output_schema=output,
            start_step="answer",
            steps=(
                AgentStep(
                    key="answer",
                    name="生成业务结果",
                    kind="model",
                    input_schema=input_schema,
                    output_schema=output,
                    inputs={"request": InputSource(source="input", path="request")},
                ),
            ),
            edges=(AgentEdge(source="answer", target="END"),),
        )
        result.append(AgentTemplate(key=key, name=name, workflow_type=kind, definition=definition))
    return result
