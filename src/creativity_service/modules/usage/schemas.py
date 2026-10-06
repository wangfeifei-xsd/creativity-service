"""用量计价与管理传输结构；运行来源只由受信服务提供。"""

from datetime import datetime
from decimal import Decimal
from typing import Annotated, Any, Literal

from pydantic import AwareDatetime, BeforeValidator, Field, field_validator

from creativity_service.core.context import Scope
from creativity_service.core.primitives import Contract, Identifier, Money, Revision


def decimal_input(value: Any) -> Any:
    if isinstance(value, float):
        raise ValueError("金额与汇率请使用十进制字符串")
    return value


Quantity = Annotated[
    Decimal,
    BeforeValidator(decimal_input),
    Field(ge=0, max_digits=24, decimal_places=8, allow_inf_nan=False),
]
Purpose = Literal["production", "debug", "evaluation"]


class PriceItem(Contract):
    dimension: str = Field(pattern=r"^[a-z][a-z_]{0,63}$")
    amount: Quantity
    per_units: int = Field(ge=1, le=1_000_000_000, strict=True)

    @field_validator("amount", mode="before")
    @classmethod
    def decimal_only(cls, value: Any) -> Any:
        if isinstance(value, float):
            raise ValueError("金额请使用十进制字符串")
        return value


class PriceCreate(Contract):
    name: str = Field(min_length=1, max_length=128)
    currency: str = Field(pattern=r"^[A-Z]{3}$")
    items: list[PriceItem] = Field(min_length=1, max_length=32)
    subset_relations: dict[str, str] = Field(default_factory=dict)
    effective_at: AwareDatetime
    source: str = Field(min_length=1, max_length=1024)


class PriceVersionView(PriceCreate):
    id: str
    model_id: str
    created_at: datetime


class AttemptPlan(Contract):
    """候选路由与实际序列化输入估算；不能直接绑定 HTTP 请求正文。"""

    run_id: Identifier
    attempt_id: Identifier
    agent_id: Identifier
    model_id: Identifier
    connection_id: Identifier
    purpose: Purpose
    input_tokens: int = Field(ge=0, strict=True)
    max_output_tokens: int = Field(ge=0, strict=True)
    additional_upper_tokens: dict[str, int] = Field(default_factory=dict)
    subset_relations: dict[str, str] = Field(default_factory=dict)
    source_type: str = Field(default="run", min_length=1, max_length=32)
    names: dict[str, str | None] = Field(default_factory=dict)


class UsageFilter(Contract):
    start_at: AwareDatetime
    end_at: AwareDatetime
    timezone: str = "Asia/Shanghai"
    key_id: Identifier | None = None
    environment: Literal["dev", "test", "fat", "prod"] | None = None
    agent_id: Identifier | None = None
    model_id: Identifier | None = None
    actor_id: Identifier | None = None
    subject_type: Identifier | None = None
    subject_id: Identifier | None = None
    purpose: Purpose | None = None
    run_id: Identifier | None = None
    target_currency: str | None = Field(default=None, pattern=r"^[A-Z]{3}$")


class RecordView(Contract):
    id: str
    channel_id: str
    run_id: str
    attempt_id: str
    created_at: datetime
    names: dict[str, str | None]
    purpose: Purpose
    purpose_label: str
    input_tokens: int | None
    output_tokens: int | None
    cached_tokens: int | None
    reasoning_tokens: int | None
    usage_status: str
    usage_label: str
    pricing_status: str
    pricing_label: str
    amount: str | None
    currency: str | None
    state: str
    state_label: str
    outcome_label: str
    revision: int
    normalized_tokens: dict[str, int | None]
    subset_relations: dict[str, str]
    calculation: dict[str, Any]


class RecordDetail(RecordView):
    source: dict[str, Any]
    adjustments: list[dict[str, Any]]
    events: list[dict[str, Any]]


class RecordPage(Contract):
    items: list[RecordView]
    total: int
    offset: int
    limit: int


class CurrencyTotal(Contract):
    currency: str
    priced: str
    provisional: str
    conversion: dict[str, Any] | None = None


class UsageSummary(Contract):
    requests: int
    attempts: int
    success_rate: str | None
    input_tokens: int | None
    output_tokens: int | None
    missing_usage: int
    unpriced: int
    costs: list[CurrencyTotal]
    trend: list[dict[str, Any]]
    aggregate_updated_at: datetime
    ledger_watermark: datetime | None
    price_complete: bool
    timezone: str


class ExchangeRateCreate(Contract):
    base_currency: str = Field(pattern=r"^[A-Z]{3}$")
    quote_currency: str = Field(pattern=r"^[A-Z]{3}$")
    rate: Quantity
    effective_at: AwareDatetime
    source: str = Field(min_length=1, max_length=1024)


class ExportView(Contract):
    id: str
    state: str
    state_label: str
    created_at: datetime
    expires_at: datetime
    download_path: str | None
    metadata: dict[str, Any]
    error_message: str | None


class RepriceInput(Contract):
    price_version_id: Identifier
    revision: Revision


class ReservationReceipt(Contract):
    scope: Scope
    run_id: str
    attempt_id: str
    reservation_ids: list[str]
    upper_cost: Money | None
    state: Literal["HELD", "PENDING", "SETTLED", "RELEASED"]


class PlatformExportCreate(Contract):
    channel_ids: list[Identifier] = Field(min_length=1, max_length=100)
    query: UsageFilter
