"""固定流程入口注册，不装载用户代码；执行实现由 17 和场景单元提供。"""

from creativity_service.modules.agents.schemas import (
    AgentDefinition,
    AgentEdge,
    AgentStep,
    AgentTemplate,
    InputSource,
    WorkflowType,
)

ENTRYPOINTS: dict[str, tuple[WorkflowType, str]] = {
    "structured.v1": ("structured", "单步结构化任务"),
    "matching.v1": ("template", "智能匹配"),
    "risk.v1": ("template", "风险评估"),
    "analysis.v1": ("template", "数据分析"),
    "tool_loop.v1": ("tool_loop", "受约束工具循环"),
    "stateful.v1": ("stateful", "有状态流程"),
}


def templates() -> list[AgentTemplate]:
    result = []
    for key, (kind, name) in ENTRYPOINTS.items():
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
                    "enum": ["SUCCESS", "NEEDS_INPUT", "PARTIAL"],
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
