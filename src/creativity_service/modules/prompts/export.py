"""导出供 16/17 消费的冻结草稿、渲染及证据契约。"""

import argparse
import json
from pathlib import Path

from creativity_service.modules.prompts.schemas import (
    PromptDebugDescriptor,
    PromptDebugEvidence,
    PromptDebugRun,
    PromptDependencySummary,
    PromptPortable,
    PromptRuntimeInput,
)


def main() -> None:
    parser = argparse.ArgumentParser(description="导出提示词交接契约")
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    root = Path("contracts/prompts")
    if not args.check:
        root.mkdir(exist_ok=True)
    for model in (
        PromptDebugDescriptor,
        PromptDebugEvidence,
        PromptDebugRun,
        PromptDependencySummary,
        PromptPortable,
        PromptRuntimeInput,
    ):
        path = root / f"{model.__name__}.schema.json"
        value = (
            json.dumps(model.model_json_schema(mode="serialization"), ensure_ascii=False, indent=2)
            + "\n"
        )
        if args.check:
            if not path.exists() or path.read_text() != value:
                raise SystemExit(f"提示词契约过期：{path.name}")
        else:
            path.write_text(value)


if __name__ == "__main__":
    main()
