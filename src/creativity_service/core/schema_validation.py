"""按不可变 JSON 内容复用结构规范校验，不缓存权限、状态或业务输入的校验结果。"""

import json
from functools import lru_cache
from typing import Any

from jsonschema import Draft202012Validator, FormatChecker

from creativity_service.core.primitives import ServiceError, canonical_json


class SchemaInputError(ServiceError):
    def __init__(self, code: str, message: str, fields: list[dict[str, Any]]) -> None:
        super().__init__(code, message, 422)
        self.fields = fields


def schema_errors(
    value: Any, schema: dict[str, Any], *, check_formats: bool = False
) -> list[dict[str, Any]]:
    """只返回字段路径和中文约束，不在错误中回显业务输入或解析器原文。"""
    validator = Draft202012Validator(
        schema, format_checker=FormatChecker() if check_formats else None
    )
    fields: list[dict[str, Any]] = []
    for error in validator.iter_errors(value):
        path = list(error.absolute_path)
        if error.validator == "required" and isinstance(error.instance, dict):
            fields.extend(
                {"path": [*path, name], "message": "缺少必填字段"}
                for name in error.validator_value
                if name not in error.instance
            )
        else:
            messages = {
                "type": "字段类型不正确",
                "additionalProperties": "含未获授权的字段",
                "enum": "字段取值不在允许范围",
                "minLength": f"至少输入 {error.validator_value} 个字符",
                "maxLength": f"最多输入 {error.validator_value} 个字符",
                "minimum": f"不能小于 {error.validator_value}",
                "maximum": f"不能大于 {error.validator_value}",
                "minItems": f"至少填写 {error.validator_value} 项",
                "maxItems": f"最多填写 {error.validator_value} 项",
            }
            fields.append(
                {"path": path, "message": messages.get(str(error.validator), "字段值不符合约定")}
            )
        if len(fields) >= 20:
            break
    return fields[:20]


def check_schema(schema: dict[str, Any]) -> None:
    check_encoded_schema(canonical_json(schema))


@lru_cache(maxsize=256)
def check_encoded_schema(payload: bytes) -> None:
    Draft202012Validator.check_schema(json.loads(payload))
