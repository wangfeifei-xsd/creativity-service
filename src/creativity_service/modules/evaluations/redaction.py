"""来源运行转样本时按显式字段清单保留，再移除常见凭据及直接身份文本。"""

import re
from typing import Any

SENSITIVE_KEYS = frozenset(
    {
        "password",
        "secret",
        "token",
        "api_key",
        "authorization",
        "cookie",
        "access_token",
        "refresh_token",
        "phone",
        "email",
        "身份证",
        "手机号",
        "密码",
    }
)


def redact(value: Any) -> Any:
    if isinstance(value, dict):
        return {k: "已脱敏" if k.lower() in SENSITIVE_KEYS else redact(v) for k, v in value.items()}
    if isinstance(value, list):
        return [redact(v) for v in value]
    if isinstance(value, str):
        value = re.sub(r"(?i)\bBearer\s+[A-Za-z0-9_.+/=-]+", "Bearer 已脱敏", value)
        value = re.sub(r"[A-Za-z0-9_.+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}", "已脱敏邮箱", value)
        value = re.sub(r"(?<!\d)1[3-9]\d{9}(?!\d)", "已脱敏手机号", value)
    return value
