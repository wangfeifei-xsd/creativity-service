"""严格保留范围、分页、字典名称及错误语义，不猜测变更后的字段。"""

from typing import Any

from pydantic import ValidationError

from creativity_service.core.context import Scope
from creativity_service.integrations.business.base import (
    RESULT_TYPES,
    BusinessResult,
    Operation,
    business_error,
)


def validate_result(result: BusinessResult, operation: Operation, scope: Scope) -> BusinessResult:
    try:
        if result.operation != operation or len(result.items) > 200:
            raise ValueError
        if result.has_more and not result.cursor:
            raise ValueError
        parsed = [RESULT_TYPES[operation].model_validate(item) for item in result.items]

        def check(value: Any) -> None:
            if isinstance(value, dict):
                if {"channel_id", "environment", "data_scope_id"} <= value.keys() and any(
                    value[name] != getattr(scope, name)
                    for name in ("channel_id", "environment", "data_scope_id")
                ):
                    raise ValueError
                for item in value.values():
                    check(item)
            elif isinstance(value, list):
                for item in value:
                    check(item)

        for item in parsed:
            check(item.model_dump(mode="json"))
        return result.model_copy(
            update={"items": [item.model_dump(mode="json") for item in parsed]}
        )
    except (ValidationError, ValueError):
        raise business_error("BUSINESS_CONTRACT_CHANGED") from None


def resolve_name(code: str, dictionary: dict[str, str]) -> str:
    """项目适配器仅从同一授权请求取得的字典解析名称，缺失时不回退显示编码。"""
    value = dictionary.get(code)
    if not value or not value.strip():
        raise business_error("BUSINESS_CONTRACT_CHANGED")
    return value


def map_fields(value: dict[str, Any], mapping: dict[str, str]) -> dict[str, Any]:
    """只执行管理员声明的字段重命名；源字段丢失或名称冲突均视为契约变化。"""
    if len(set(mapping.values())) != len(mapping):
        raise business_error("BUSINESS_CONTRACT_CHANGED")
    result = {key: item for key, item in value.items() if key not in mapping.values()}
    for target, source in mapping.items():
        if source not in value or target in result:
            raise business_error("BUSINESS_CONTRACT_CHANGED")
        result[target] = value[source]
    return result


def source_status(status: int) -> None:
    code = {
        401: "BUSINESS_FORBIDDEN",
        403: "BUSINESS_FORBIDDEN",
        404: "BUSINESS_NOT_FOUND",
        410: "BUSINESS_UNLISTED",
        204: "BUSINESS_NO_DATA",
        408: "BUSINESS_TIMEOUT",
        504: "BUSINESS_TIMEOUT",
        501: "BUSINESS_UNSUPPORTED",
    }.get(status)
    if code:
        raise business_error(code)
    if status < 200 or status >= 300:
        raise business_error("BUSINESS_UNAVAILABLE")
