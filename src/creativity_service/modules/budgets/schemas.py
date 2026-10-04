"""预算策略与平台限额配置。"""

from datetime import datetime
from decimal import Decimal
from typing import Literal

from pydantic import Field

from creativity_service.core.primitives import Contract, Revision
from creativity_service.modules.usage.schemas import Quantity

Period = Literal["minute", "hour", "day", "month"]
Unit = Literal["amount", "tokens", "attempts", "requests", "concurrency"]


class BudgetCreate(Contract):
    name: str = Field(min_length=1, max_length=128)
    scope_type: Literal["channel", "key", "model"]
    scope_id: str = Field(min_length=1, max_length=128)
    period: Period = "month"
    timezone: str = "Asia/Shanghai"
    currency: str | None = Field(default=None, pattern=r"^[A-Z]{3}$")
    limit_value: Quantity
    unit: Unit = "amount"
    mode: Literal["HARD", "ALERT_ONLY"] = "HARD"
    thresholds: list[Decimal] = Field(default_factory=lambda: [Decimal("0.8"), Decimal("1")])
    status: Literal["ACTIVE", "DISABLED"] = "ACTIVE"


class BudgetUpdate(BudgetCreate):
    revision: Revision


class BudgetView(BudgetCreate):
    id: str
    version_id: str
    revision: int
    mode_label: str
    unit_label: str
    scope_name: str | None
    used: str | None
    remaining: str | None
    blocked_reason: str | None = None


class PlatformLimitCreate(Contract):
    limit_code: str = Field(pattern=r"^[a-z][a-z0-9_-]{0,63}$")
    name: str = Field(min_length=1, max_length=128)
    unit: Literal["requests", "concurrency"]
    period: Period = "minute"
    timezone: str = "Asia/Shanghai"
    limit_value: int = Field(ge=1, strict=True)
    status: Literal["ACTIVE", "DISABLED"] = "ACTIVE"
    revision: Revision | None = None


class PlatformLimitView(PlatformLimitCreate):
    revision: Revision
    unit_label: str
    used: int | None
    remaining: int | None
    effective_at: datetime
