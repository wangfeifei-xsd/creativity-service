"""导出渠道凭据接入契约；历史委托签名不再作为业务接入协议发布。"""

import argparse
import json
from pathlib import Path
from typing import Any

from creativity_service.modules.channels.schemas import TokenExchange


def artifacts() -> dict[str, object]:
    from creativity_service.app import create_schema_app

    app = create_schema_app()
    schema = app.openapi()
    schema["info"] = {"title": "渠道 Key 与密钥业务接入", "version": "3.0.0"}
    schema["paths"] = {
        p: v
        for p, v in schema["paths"].items()
        if p.startswith(
            (
                "/api/v1/auth/token",
                "/api/v1/runs",
                "/api/v1/conversations",
            )
        )
    }
    all_definitions = schema["components"]["schemas"]
    retained: dict[str, Any] = {}

    def retain_refs(value: Any) -> None:
        if isinstance(value, dict):
            reference = value.get("$ref", "")
            if reference.startswith("#/components/schemas/"):
                name = reference.rsplit("/", 1)[1]
                if name not in retained:
                    retained[name] = all_definitions[name]
                    retain_refs(retained[name])
            for child in value.values():
                retain_refs(child)
        elif isinstance(value, list):
            for child in value:
                retain_refs(child)

    retain_refs(schema["paths"])
    schema["components"]["schemas"] = retained
    return {
        "TokenExchange.schema.json": TokenExchange.model_json_schema(mode="serialization"),
        "openapi-v3.json": schema,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="导出业务接入契约")
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    root = Path("contracts/integrations")
    if not args.check:
        root.mkdir(parents=True, exist_ok=True)
    for filename, value in artifacts().items():
        path = root / filename
        content = json.dumps(value, ensure_ascii=False, indent=2) + "\n"
        if args.check:
            if not path.exists() or path.read_text() != content:
                raise SystemExit(f"业务接入契约过期：{filename}")
        else:
            path.write_text(content)


if __name__ == "__main__":
    main()
