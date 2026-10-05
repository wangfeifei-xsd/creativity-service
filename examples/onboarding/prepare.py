"""将受控 MCP 契约制成可导入技能包与 Agent 配置，不注册平台业务模块。"""

import argparse
import copy
import hashlib
import json
import zipfile
from pathlib import Path

from examples.mcp.server import fixture


def prepare(source, folder):
    folder.mkdir(parents=True, exist_ok=True)
    settings = {
        "loading_mode": "mandatory",
        "context_budget": 12000,
        "tool_requirements": [
            {
                "tool_code": source.tool_name,
                "version_label": "初始版本",
                "source_type": "mcp",
                "input_schema": source.input_schema,
                "output_schema": source.output_schema,
            }
        ],
    }
    files = {
        "SKILL.md": (
            "---\nname: configured-source\ndescription: 根据授权源结果组织输出。\n---\n\n"
            "将查询步骤的真实结构化数据写入 data，保留来源引用，不添加源端未返回的数据。"
            "空结果与错误遵守源服务契约。参考 [输出规则](references/output.md)。\n"
        ),
        "references/output.md": "仅使用本次已授权的工具结果。源版本和观测时间来自 MCP。\n",
        ".platform/skill.json": json.dumps({"format": "skill-package-v1", "settings": settings}),
    }
    archive = folder / "skill.zip"
    with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_DEFLATED) as package:
        for name, value in files.items():
            package.writestr(zipfile.ZipInfo(name, date_time=(2026, 10, 3, 0, 0, 0)), value)
    definition = json.loads((Path(__file__).parents[1] / "agents/archive-answer.json").read_text())[
        "definition"
    ]
    definition["input_schema"] = copy.deepcopy(source.input_schema)
    # 与管理向导的通用默认值及本次 180 秒运行限时一致。
    definition["limits"].update(deadline_seconds=180, max_model_rounds=6, max_tool_calls=10)
    output = definition["output_schema"]
    output["properties"]["data"] = copy.deepcopy(source.output_schema)
    lookup, answer, deliver = definition["steps"]
    lookup.update(
        name="查询授权源",
        dependency="bind_tool",
        input_schema=source.input_schema,
        output_schema=source.output_schema,
        inputs={key: {"source": "input", "path": key} for key in source.input_schema["properties"]},
    )
    answer.update(
        name="组织结构化输出",
        input_schema=source.output_schema,
        output_schema=output,
        inputs={
            key: {"source": "step", "step": "lookup", "path": key}
            for key in source.output_schema["properties"]
        },
    )
    deliver.update(input_schema=output, output_schema=output)
    definition["bindings"] = {
        "prompt_version": "bind_prompt",
        "model_route_version": "bind_model_route",
        "tool_versions": ["bind_tool"],
        "skill_versions": ["bind_skill"],
        "skill_loading": [
            {
                "version_id": "bind_skill",
                "loading_mode": "mandatory",
                "selected_files": ["references/output.md"],
            }
        ],
    }
    (folder / "agent.json").write_text(json.dumps(definition, ensure_ascii=False, indent=2) + "\n")
    return archive, definition


def main() -> None:
    parser = argparse.ArgumentParser(description="生成三渠道验收配置")
    parser.add_argument("--output", type=Path, default=Path(".local/examples/onboarding"))
    args = parser.parse_args()
    scenarios = json.loads(Path(__file__).with_name("scenarios.json").read_text())
    manifest = []
    for scenario in scenarios:
        source = fixture(scenario["profile"])
        source.input_schema = copy.deepcopy(scenario.get("input_schema", source.input_schema))
        folder = args.output / scenario["code"]
        prepare(source, folder)
        manifest.append(
            {
                "scenario": scenario["code"],
                "files": {
                    name: hashlib.sha256((folder / name).read_bytes()).hexdigest()
                    for name in ("skill.zip", "agent.json")
                },
            }
        )
    (args.output / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n"
    )


if __name__ == "__main__":
    main()
