"""固定结构化属性边界；实时数据与凭据不能通过自由属性绕过校验。"""

import re
from datetime import datetime, timedelta
from typing import Any

from jsonschema import Draft202012Validator

from creativity_service.core.primitives import ServiceError, canonical_json, utcnow
from creativity_service.modules.memory.schemas import TYPES, MemoryAttribute, MemoryPolicy

TEXT = {"type": "string", "minLength": 1, "maxLength": 100}
ATTRIBUTES = [
    MemoryAttribute(
        key="favorite_games",
        label="常玩游戏",
        memory_type="PREFERENCE",
        value_schema={"type": "array", "items": TEXT, "minItems": 1, "maxItems": 20},
    ),
    MemoryAttribute(
        key="play_style", label="偏好玩法", memory_type="PREFERENCE", value_schema=TEXT
    ),
    MemoryAttribute(
        key="usual_budget",
        label="通常预算",
        memory_type="PREFERENCE",
        value_schema={
            "type": "object",
            "required": ["min", "max", "currency"],
            "additionalProperties": False,
            "properties": {
                "min": {"type": "integer", "minimum": 0, "maximum": 1000000},
                "max": {"type": "integer", "minimum": 0, "maximum": 1000000},
                "currency": {"const": "CNY"},
            },
        },
    ),
    MemoryAttribute(
        key="preferred_hours", label="常用时段", memory_type="PREFERENCE", value_schema=TEXT
    ),
    MemoryAttribute(
        key="membership_level", label="会员等级", memory_type="FACT", value_schema=TEXT
    ),
]
ATTRIBUTE_MAP = {a.key: a for a in ATTRIBUTES}
SECRET = re.compile(
    r"密码|口令|支付凭据|私钥|模型密钥|api[_ -]?key|password|secret|bearer\s|sk-[\w-]{8,}", re.I
)


def validate_value(key: str, memory_type: str, value: Any, policy: MemoryPolicy) -> MemoryAttribute:
    if memory_type not in TYPES or memory_type not in policy.allowed_types:
        raise ServiceError("MEMORY_TYPE_NOT_ALLOWED", "不允许保存此记忆类型", 422)
    attribute = ATTRIBUTE_MAP.get(key)
    if attribute is None or attribute.memory_type != memory_type:
        raise ServiceError("MEMORY_ATTRIBUTE_NOT_ALLOWED", "此属性不属于可保存的长期记忆", 422)
    if not Draft202012Validator(attribute.value_schema).is_valid(value):
        raise ServiceError("MEMORY_VALUE_INVALID", "记忆值不符合属性格式", 422)
    if key == "usual_budget" and value["min"] > value["max"]:
        raise ServiceError("MEMORY_VALUE_INVALID", "预算下限不能大于上限", 422)
    if SECRET.search(canonical_json(value).decode()):
        raise ServiceError("MEMORY_SENSITIVE_VALUE", "记忆不能包含密码或访问凭据", 422)
    return attribute


def validate_policy(policy: MemoryPolicy, channel: MemoryPolicy | None = None) -> None:
    if not set(policy.allowed_types) <= TYPES.keys() or len(set(policy.allowed_types)) != len(
        policy.allowed_types
    ):
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


def value_label(key: str, value: Any) -> str:
    if value is None:
        return "内容已清除"
    if key == "usual_budget":
        return f"{value['min']}–{value['max']} 元"
    return "、".join(value) if isinstance(value, list) else str(value)
