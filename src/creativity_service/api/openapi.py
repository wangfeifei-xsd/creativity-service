"""导出可重复生成的 OpenAPI 契约。"""

import argparse
import json
from pathlib import Path
from typing import Any

from creativity_service.app import create_schema_app


def build_schema() -> dict[str, Any]:
    return create_schema_app().openapi()


def main() -> None:
    parser = argparse.ArgumentParser(description="离线导出接口契约")
    parser.add_argument("--output", type=Path, default=Path("contracts/openapi.json"))
    parser.add_argument("--check", action="store_true", help="只校验已提交契约是否最新")
    args = parser.parse_args()
    content = json.dumps(build_schema(), ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    if args.check:
        if not args.output.exists() or args.output.read_text(encoding="utf-8") != content:
            raise SystemExit("OpenAPI 契约已变化，请执行 uv run creativity-openapi")
        return
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(content, encoding="utf-8")


if __name__ == "__main__":
    main()
