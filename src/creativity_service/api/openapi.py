"""导出可重复生成的 OpenAPI 契约。"""

import argparse
import json
from pathlib import Path
from typing import Any

from creativity_service.app import create_schema_app


def build_schema(*, backend: bool = False) -> dict[str, Any]:
    schema = create_schema_app().openapi()
    if not backend:
        return schema
    # 对外契约从正式路由裁剪，避免另建一套接入实现或引入管理接口。
    schema["info"] = {"title": "Creativity 统一 AI 接口", "version": "1.0.0"}
    schema["paths"] = {p: v for p, v in schema["paths"].items() if p.startswith("/api/v1/")}
    definitions = schema["components"]["schemas"]
    retained: dict[str, Any] = {}

    def retain(value: Any) -> None:
        if isinstance(value, dict):
            reference = value.get("$ref", "")
            if reference.startswith("#/components/schemas/"):
                name = reference.rsplit("/", 1)[1]
                if name not in retained:
                    retained[name] = definitions[name]
                    retain(retained[name])
            for child in value.values():
                retain(child)
        elif isinstance(value, list):
            for child in value:
                retain(child)

    retain(schema["paths"])
    schema["components"]["schemas"] = retained
    return schema


def main() -> None:
    parser = argparse.ArgumentParser(description="离线导出接口契约")
    parser.add_argument("--output", type=Path)
    parser.add_argument("--backend", action="store_true", help="仅导出业务后端统一接口")
    parser.add_argument("--check", action="store_true", help="只校验已提交契约是否最新")
    args = parser.parse_args()
    args.output = args.output or Path(
        "contracts/backend/openapi-v1.json" if args.backend else "contracts/openapi.json"
    )
    content = (
        json.dumps(build_schema(backend=args.backend), ensure_ascii=False, indent=2, sort_keys=True)
        + "\n"
    )
    if args.check:
        if not args.output.exists() or args.output.read_text(encoding="utf-8") != content:
            raise SystemExit("OpenAPI 契约已变化，请执行 uv run creativity-openapi")
        return
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(content, encoding="utf-8")


if __name__ == "__main__":
    main()
