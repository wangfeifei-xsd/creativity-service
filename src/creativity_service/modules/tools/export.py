"""离线导出工具绑定、运行授权与统一执行入口契约。"""

import argparse
import json
from pathlib import Path

from creativity_service.integrations.tools import AdapterResult
from creativity_service.modules.tools.schemas import RunToolGrant, ToolDefinition, ToolExecution


def main() -> None:
    parser = argparse.ArgumentParser(description="导出工具交接契约")
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    root = Path("contracts/tools")
    if not args.check:
        root.mkdir(exist_ok=True)
    for model in (AdapterResult, RunToolGrant, ToolDefinition, ToolExecution):
        path = root / f"{model.__name__}.schema.json"
        value = (
            json.dumps(model.model_json_schema(mode="serialization"), ensure_ascii=False, indent=2)
            + "\n"
        )
        if args.check:
            if not path.exists() or path.read_text() != value:
                raise SystemExit(f"工具契约过期：{path.name}")
        else:
            path.write_text(value)


if __name__ == "__main__":
    main()
