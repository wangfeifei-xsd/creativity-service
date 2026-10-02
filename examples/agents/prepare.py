"""离线替换样例中的资源占位符；实际渠道、授权和契约继续由管理 API 校验。"""

import argparse
import json
from pathlib import Path
from typing import Any

from creativity_service.modules.agents.schemas import AgentCreate
from creativity_service.modules.agents.validation import static_issues


def prepare(template: dict[str, Any], bindings: dict[str, str]) -> AgentCreate:
    used: set[str] = set()

    def replace(value: Any) -> Any:
        if isinstance(value, str) and value.startswith("bind_"):
            used.add(value)
            if value not in bindings:
                raise ValueError("缺少显式资源绑定：" + value)
            return bindings[value]
        if isinstance(value, dict):
            return {key: replace(item) for key, item in value.items()}
        if isinstance(value, list):
            return [replace(item) for item in value]
        return value

    body = AgentCreate.model_validate(replace(template))
    if set(bindings) != used:
        raise ValueError("绑定清单包含未使用的资源")
    issues = static_issues(body.definition)
    if issues:
        raise ValueError("；".join(item.message for item in issues))
    return body


def main() -> None:
    parser = argparse.ArgumentParser(description="准备可提交的 Agent 配置")
    parser.add_argument("template", type=Path)
    parser.add_argument("bindings", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    body = prepare(json.loads(args.template.read_text()), json.loads(args.bindings.read_text()))
    args.output.write_text(body.model_dump_json(indent=2) + "\n")


if __name__ == "__main__":
    main()
