"""系统渠道的初始配置，供显式初始化服务与空库 SQL 归档共用。"""

from typing import Any


def system_channel_values() -> dict[str, Any]:
    """每次返回独立配置；身份、时间和修订由各初始化入口显式赋值。"""
    return {
        "channel_code": "system",
        "name": "平台系统渠道",
        "owner": "平台",
        "status": "ACTIVE",
        "archived_at": None,
        "retention_policy": {"retention_days": 365},
        "budget_policy_refs": [],
        "rate_limit_policy_refs": [],
    }
