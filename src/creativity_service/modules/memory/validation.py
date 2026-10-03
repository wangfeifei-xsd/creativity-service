"""画像属性由渠道登记，平台只提供通用属性和结构、安全校验。"""

import re
from datetime import datetime, timedelta
from typing import Any

from jsonschema import Draft202012Validator, SchemaError

from creativity_service.core.primitives import ServiceError, canonical_json, utcnow
from creativity_service.modules.memory.schemas import TYPES, MemoryAttribute, MemoryPolicy

TEXT = {"type": "string", "minLength": 1, "maxLength": 100}
ATTRIBUTES = [
    MemoryAttribute(key="preferred_language", label="偏好语言", value_schema=TEXT),
    MemoryAttribute(key="communication_style", label="沟通偏好", value_schema=TEXT),
    MemoryAttribute(
        key="interests",
        label="长期兴趣",
        value_schema={
            "type": "array",
            "items": TEXT,
            "minItems": 1,
            "maxItems": 20,
        },
    ),
    MemoryAttribute(key="time_preferences", label="时间偏好", value_schema=TEXT),
]
SECRET = re.compile(
    r"密码|口令|支付凭据|私钥|模型密钥|api[_ -]?key|password|secret|bearer\s|sk-[\w-]{8,}", re.I
)


def validate_value(
    key: str, memory_type: str, value: Any, policy: MemoryPolicy, attributes: list[MemoryAttribute]
) -> MemoryAttribute:
    if memory_type not in TYPES or memory_type not in policy.allowed_types:
        raise ServiceError("MEMORY_TYPE_NOT_ALLOWED", "不允许保存此记忆类型", 422)
    attribute = next((a for a in attributes if a.key == key), None)
    if attribute is None or attribute.memory_type != memory_type:
        raise ServiceError("MEMORY_ATTRIBUTE_NOT_ALLOWED", "此属性不属于可保存的长期记忆", 422)
    if not Draft202012Validator(attribute.value_schema).is_valid(value):
        raise ServiceError("MEMORY_VALUE_INVALID", "记忆值不符合属性格式", 422)
    if len(canonical_json(value)) > 16000:
        raise ServiceError("MEMORY_VALUE_INVALID", "记忆值过长", 422)
    if SECRET.search(canonical_json(value).decode()):
        raise ServiceError("MEMORY_SENSITIVE_VALUE", "记忆不能包含密码或访问凭据", 422)
    return attribute


def validate_policy(policy: MemoryPolicy, channel: MemoryPolicy | None = None) -> None:
    if not set(policy.allowed_types) <= {"PREFERENCE", "FACT"} or len(
        set(policy.allowed_types)
    ) != len(policy.allowed_types):
        raise ServiceError("MEMORY_TYPE_NOT_ALLOWED", "允许记忆类型无效或重复", 422)
    if policy.retrieval_limit > policy.max_items:
        raise ServiceError("MEMORY_POLICY_INVALID", "检索条数不能超过存储条数上限", 422)
    modes = {"DISABLED": 0, "CANDIDATE": 1, "EXPLICIT": 2}
    if channel and (
        not set(policy.allowed_types) <= set(channel.allowed_types)
        or (policy.read_enabled and not channel.read_enabled)
        or (policy.suggest_enabled and not channel.suggest_enabled)
        or modes[policy.write_mode] > modes[channel.write_mode]
        or any(
            getattr(policy, f) > getattr(channel, f)
            for f in ("ttl_seconds", "max_items", "retrieval_limit")
        )
    ):
        raise ServiceError("MEMORY_POLICY_EXCEEDS_CHANNEL", "智能体策略不能扩大渠道记忆授权", 422)


def expiry(policy: MemoryPolicy, requested: datetime | None, observed: datetime) -> datetime:
    ceiling = observed + timedelta(seconds=policy.ttl_seconds)
    result = requested or ceiling
    if result <= utcnow() or result > ceiling:
        raise ServiceError("MEMORY_TTL_INVALID", "有效期必须晚于当前时间且不超过策略上限", 422)
    return result


def validate_attributes(attributes: list[MemoryAttribute]) -> None:
    if len({a.key for a in attributes}) != len(attributes):
        raise ServiceError("MEMORY_ATTRIBUTE_INVALID", "画像属性不能重复", 422)
    for attribute in attributes:
        if attribute.key.startswith("archive_") or SECRET.search(attribute.key + attribute.label):
            raise ServiceError("MEMORY_ATTRIBUTE_INVALID", "画像属性包含保留标识或敏感内容", 422)
        try:
            Draft202012Validator.check_schema(attribute.value_schema)
        except SchemaError as exc:
            raise ServiceError("MEMORY_ATTRIBUTE_INVALID", "画像属性格式定义无效", 422) from exc
        # 禁止远端引用，防止校验属性时产生外部请求。
        if any(
            f'"{key}"' in canonical_json(attribute.value_schema).decode()
            for key in ("$ref", "$dynamicRef", "$recursiveRef")
        ):
            raise ServiceError("MEMORY_ATTRIBUTE_INVALID", "画像属性不支持引用其他结构", 422)


def value_label(key: str, value: Any) -> str:
    if value is None:
        return "内容已清除"
    return value if isinstance(value, str) else canonical_json(value).decode()
