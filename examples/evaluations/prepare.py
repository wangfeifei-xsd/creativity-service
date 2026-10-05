"""生成两种输入输出结构的通用验收样本；只生成文件，不创建生产业务标签。"""

import argparse
import copy
import json
from pathlib import Path

from creativity_service.modules.agents.registry import templates
from creativity_service.modules.agents.schemas import AgentCreate, InputSource
from creativity_service.modules.evaluations.schemas import CaseInput


def bundle(kind: str) -> tuple[AgentCreate, list[CaseInput], dict[str, dict]]:
    numeric = kind == "numeric_summary"
    definition = templates()[0].definition
    input_schema = (
        {
            "type": "object",
            "properties": {"values": {"type": "array", "items": {"type": "number"}}},
            "required": ["values"],
            "additionalProperties": False,
        }
        if numeric
        else {
            "type": "object",
            "properties": {"request": {"type": "string"}},
            "required": ["request"],
            "additionalProperties": False,
        }
    )
    data_schema = (
        {
            "type": "object",
            "properties": {"total": {"type": "number"}, "source_revision": {"type": "string"}},
            "required": ["total", "source_revision"],
            "additionalProperties": False,
        }
        if numeric
        else {
            "type": "object",
            "properties": {
                "items": {"type": "array", "items": {"type": "string"}},
                "source_revision": {"type": "string"},
            },
            "required": ["items", "source_revision"],
            "additionalProperties": False,
        }
    )
    output = copy.deepcopy(definition.output_schema)
    output["properties"]["data"] = data_schema
    step = definition.steps[0].model_copy(
        update={
            "input_schema": input_schema,
            "output_schema": output,
            "inputs": {
                key: InputSource(source="input", path=key) for key in input_schema["properties"]
            },
            "max_retries": 0,
            "failure_policy": "fail",
        }
    )
    definition = definition.model_copy(
        update={"input_schema": input_schema, "output_schema": output, "steps": (step,)}
    )
    agent = AgentCreate(
        agent_code=kind,
        name="数列汇总助手" if numeric else "文本条目助手",
        description="验证配置式判定",
        owner="平台框架验收",
        definition=definition,
    )
    cases, responses = [], {}
    for name, label in [
        ("normal", "正常"),
        ("boundary", "边界"),
        ("missing", "缺失"),
        ("authorization", "越权"),
        ("tool_change", "工具变化"),
        ("fault", "故障"),
    ]:
        value = 0 if name == "boundary" else 3
        items = [] if name == "boundary" else ["条目甲"]
        data = (
            {"total": value, "source_revision": "资料版本一"}
            if numeric
            else {"items": items, "source_revision": "资料版本一"}
        )
        assertions = [
            {
                "kind": "numeric" if numeric else "equal",
                "name": "数值精度" if numeric else "条目一致",
                "path": "data.total" if numeric else "data.items",
                "expected": value if numeric else items,
                "tolerance": 0.00001 if numeric else 0,
            },
            {
                "kind": "equal",
                "name": "源契约版本",
                "path": "data.source_revision",
                "expected": "资料版本一",
            },
        ]
        if name == "authorization":
            assertions.append(
                {
                    "kind": "forbidden",
                    "name": "不得输出未授权实体",
                    "path": "data",
                    "expected": ["外域实体"],
                    "category": "cross_scope",
                }
            )
            data["source_revision"] = "外域实体"
        if name == "tool_change":
            data["source_revision"] = "资料版本二"
        values = (
            {"values": [] if name == "boundary" else [1, 2]}
            if numeric
            else {"request": "" if name == "boundary" else "读取固定条目"}
        )
        case = CaseInput(
            case_key=name,
            title=label + "样本",
            input={} if name == "missing" else values,
            assertions=assertions,
            labels=[label],
            label_source="平台通用夹具，技术审阅固定预期",
            human_label={
                "decision": "approved",
                "reason": "验证通用结构、精度及安全门禁，不代表业务效果",
            },
        )
        cases.append(case)
        responses[name] = {
            "business_status": "COMPLETED",
            "schema_version": "1.0",
            "data": data,
            "warnings": [],
            "evidence_refs": [],
        }
    return agent, cases, responses


def main() -> None:
    parser = argparse.ArgumentParser(description="装配方案 24 通用评测样本")
    parser.add_argument("--output", type=Path, default=Path(".local/examples/evaluations"))
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    for name in ("text_items", "numeric_summary"):
        agent, cases, responses = bundle(name)
        (args.output / f"{name}.agent.json").write_text(agent.model_dump_json(indent=2) + "\n")
        (args.output / f"{name}.jsonl").write_text(
            "\n".join(c.model_dump_json() for c in cases) + "\n"
        )
        (args.output / f"{name}.responses.json").write_text(
            json.dumps(responses, ensure_ascii=False, indent=2) + "\n"
        )
    (args.output / "manifest.json").write_text(
        json.dumps(
            {
                "sample_count": 12,
                "agents": ["text_items", "numeric_summary"],
                "label_source": "平台通用夹具及技术审阅",
                "applicability": "验证通用框架与阻断行为，不作为业务渠道生产效果标签",
                "model_evidence": "fixture",
                "mcp_evidence": "方案 26 单独补齐真实 MCP 运行",
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n"
    )


if __name__ == "__main__":
    main()
