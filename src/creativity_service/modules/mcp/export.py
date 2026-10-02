"""离线导出 MCP 发现与本地工具导入交接契约。"""

import argparse
import json
from pathlib import Path

from creativity_service.modules.mcp.schemas import McpDiff, McpDiscovery, McpImport, McpImportInput


def main() -> None:
    parser = argparse.ArgumentParser(description="导出 MCP 交接契约")
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    root = Path("contracts/mcp")
    if not args.check:
        root.mkdir(exist_ok=True)
    for model in (McpDiscovery, McpImportInput, McpImport, McpDiff):
        path = root / f"{model.__name__}.schema.json"
        value = (
            json.dumps(model.model_json_schema(mode="serialization"), ensure_ascii=False, indent=2)
            + "\n"
        )
        if args.check:
            if not path.exists() or path.read_text() != value:
                raise SystemExit(f"MCP 契约过期：{path.name}")
        else:
            path.write_text(value)


if __name__ == "__main__":
    main()
