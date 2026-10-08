"""按模块保存独立契约，统一生成与过期检查。"""

import json
from collections.abc import Iterable
from pathlib import Path
from typing import Any

from pydantic import BaseModel
from pydantic.json_schema import JsonSchemaMode


def schema_bundle(
    models: Iterable[type[BaseModel]], *, mode: JsonSchemaMode = "serialization"
) -> dict[str, Any]:
    # 每个值都是独立 schema，内部 $ref 仍以该值为根解析，不能跨模型合并 $defs。
    return {model.__name__: model.model_json_schema(mode=mode) for model in models}


def write_contract(path: Path, value: object, *, check: bool = False) -> None:
    content = json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    if check:
        if not path.exists() or path.read_text(encoding="utf-8") != content:
            raise SystemExit(f"契约过期：{path}")
    else:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
