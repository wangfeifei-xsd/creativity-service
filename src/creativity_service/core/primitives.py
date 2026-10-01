"""公共值类型、错误及确定性请求摘要。"""

import hashlib
import json
import re
from datetime import UTC, datetime
from decimal import Decimal
from typing import Annotated, Any
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, JsonValue, field_serializer, field_validator

Identifier = Annotated[
    str, Field(min_length=1, max_length=128, pattern=r"^[A-Za-z0-9][A-Za-z0-9_.-]*$")
]
Digest = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
Revision = Annotated[int, Field(ge=1, strict=True)]


class Contract(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, hide_input_in_errors=True)


class ServiceError(Exception):
    def __init__(self, code: str, message: str, status: int = 409) -> None:
        super().__init__(message)
        self.code, self.message, self.status = code, message, status


def unavailable(component: str) -> ServiceError:
    return ServiceError("DEPENDENCY_UNAVAILABLE", f"{component}暂不可用", 503)


def new_id(prefix: str = "obj") -> str:
    if not re.fullmatch(r"[a-z][a-z0-9_]{0,20}", prefix):
        raise ValueError("标识前缀不合法")
    return f"{prefix}_{uuid4().hex}"


def utcnow() -> datetime:
    return datetime.now(UTC)


def canonical_json(value: Any) -> bytes:
    """摘要协议 v1：键排序、UTF-8、无空白，不转换字符串或丢弃业务字段。"""

    def validate(item: Any) -> None:
        if isinstance(item, dict):
            if any(not isinstance(key, str) for key in item):
                raise ValueError("JSON 对象的键必须为字符串")
            for nested in item.values():
                validate(nested)
        elif isinstance(item, list):
            for nested in item:
                validate(nested)
        elif item is not None and type(item) not in (str, int, float, bool):
            raise ValueError("摘要仅接受 JSON 类型；金额须先转十进制字符串")

    validate(value)
    return json.dumps(
        value, sort_keys=True, ensure_ascii=False, separators=(",", ":"), allow_nan=False
    ).encode("utf-8")


def digest(value: Any) -> str:
    return hashlib.sha256(canonical_json(value)).hexdigest()


class RunInput(Contract):
    agent_code: Identifier
    input: dict[str, JsonValue]
    conversation_id: Identifier | None = None
    delivery: str = Field(default="async", pattern="^(async|sync|stream)$")

    def semantic_digest(self) -> str:
        # 认证来自独立上下文；只剔除顶层返回方式，保留业务 input 中同名字段。
        return digest({"algorithm": "request-v1", **self.model_dump(exclude={"delivery"})})


class Money(Contract):
    amount: Decimal = Field(max_digits=24, decimal_places=8, allow_inf_nan=False)
    currency: str = Field(pattern=r"^[A-Z]{3}$")

    @field_serializer("amount")
    def serialize_amount(self, value: Decimal) -> str:
        return format(value, "f")

    @field_validator("amount", mode="before")
    @classmethod
    def reject_float(cls, value: Any) -> Any:
        if isinstance(value, float):
            raise ValueError("金额使用十进制字符串，不能传浮点数")
        return value


class PageRequest(Contract):
    limit: int = Field(default=50, ge=1, le=200)
    cursor: str | None = Field(default=None, max_length=2048)


class Page[T](Contract):
    items: list[T]
    next_cursor: str | None
    has_more: bool
