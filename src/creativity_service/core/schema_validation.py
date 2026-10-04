"""按不可变 JSON 内容复用结构规范校验，不缓存权限、状态或业务输入的校验结果。"""

import json
from functools import lru_cache
from typing import Any

from jsonschema import Draft202012Validator

from creativity_service.core.primitives import canonical_json


def check_schema(schema: dict[str, Any]) -> None:
    check_encoded_schema(canonical_json(schema))


@lru_cache(maxsize=256)
def check_encoded_schema(payload: bytes) -> None:
    Draft202012Validator.check_schema(json.loads(payload))
