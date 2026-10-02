"""导出配置与执行交接契约，不将内部冻结结构绑定到公开请求。"""

import argparse
import json
from pathlib import Path

from creativity_service.modules.agents.schemas import (
    AgentDefinition,
    AgentValidation,
    FrozenExecutionSpec,
)
from creativity_service.modules.releases.ports import EvaluationEvidence


def main() -> None:
    parser = argparse.ArgumentParser(description="导出智能体契约")
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    root = Path("contracts/agents")
    if not args.check:
        root.mkdir(parents=True, exist_ok=True)
    for model in (AgentDefinition, AgentValidation, FrozenExecutionSpec, EvaluationEvidence):
        path = root / f"{model.__name__}.schema.json"
        content = json.dumps(model.model_json_schema(), ensure_ascii=False, indent=2) + "\n"
        if args.check:
            if not path.exists() or path.read_text() != content:
                raise SystemExit(f"智能体契约过期：{path}")
        else:
            path.write_text(content)


if __name__ == "__main__":
    main()
