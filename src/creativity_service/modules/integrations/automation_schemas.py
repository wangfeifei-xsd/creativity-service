"""通用定时、批量受理和 Webhook 的有界输入契约。"""

from datetime import UTC, datetime, time, timedelta
from typing import Any, Literal, Self
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import Field, SecretStr, model_validator
from pydantic_core import PydanticCustomError

from creativity_service.core.primitives import (
    Contract,
    Identifier,
    Revision,
    RunInput,
    canonical_json,
)


class ScheduleCreate(Contract):
    name: str = Field(min_length=1, max_length=128)
    request: RunInput
    timezone: str = "Asia/Shanghai"
    interval_seconds: int | None = Field(default=None, ge=60, le=2592000, strict=True)
    daily_at: str | None = Field(default=None, pattern=r"^([01]\d|2[0-3]):[0-5]\d$")

    @model_validator(mode="after")
    def valid(self) -> Self:
        try:
            ZoneInfo(self.timezone)
        except ZoneInfoNotFoundError as exc:
            raise PydanticCustomError("timezone_invalid", "时区不存在") from exc
        if (self.interval_seconds is None) == (self.daily_at is None):
            raise ValueError("请选择固定间隔或每日时间之一")
        if self.request.conversation_id or len(canonical_json(self.request.input)) > 262144:
            raise ValueError("计划输入超过上限或绑定了会话")
        return self


def next_window(spec: ScheduleCreate, after: datetime) -> datetime:
    """只计算未来窗口；夏令时缺失时间跳过，重复时间仅使用首次。"""
    if spec.interval_seconds is not None:
        return after + timedelta(seconds=spec.interval_seconds)
    zone = ZoneInfo(spec.timezone)
    hour, minute = map(int, (spec.daily_at or "").split(":"))
    local = after.astimezone(zone)
    for offset in range(370):
        candidate = datetime.combine(
            local.date() + timedelta(days=offset), time(hour, minute), zone
        )
        absolute = candidate.astimezone(UTC)
        if absolute > after and absolute.astimezone(zone).replace(tzinfo=None) == candidate.replace(
            tzinfo=None
        ):
            return absolute
    raise ValueError("无法确定下一触发窗口")


class Toggle(Contract):
    revision: Revision
    active: bool


class BatchItem(Contract):
    event_id: str = Field(min_length=1, max_length=128)
    request: RunInput

    @model_validator(mode="after")
    def independent(self) -> Self:
        if self.request.conversation_id:
            raise ValueError("批量条目不能绑定会话")
        return self


class BatchCreate(Contract):
    name: str = Field(min_length=1, max_length=128)
    items: list[BatchItem] = Field(min_length=1, max_length=100)

    @model_validator(mode="after")
    def bounded(self) -> Self:
        if len({item.event_id for item in self.items}) != len(self.items):
            raise PydanticCustomError("batch_event_duplicate", "同批次事件编号不能重复")
        if len(canonical_json(self.model_dump(mode="json"))) > 1048576:
            raise ValueError("批次输入超过一兆字节")
        return self


class BatchCancel(Contract):
    revision: Revision
    cancel_runs: bool = True


class WebhookCreate(Contract):
    name: str = Field(min_length=1, max_length=128)
    url: str = Field(min_length=1, max_length=2048)
    secret: SecretStr = Field(min_length=32, max_length=256)
    client_ids: list[Identifier] = Field(default_factory=list, max_length=100)
    events: tuple[Literal["run.terminal", "alert.triggered", "alert.resolved"], ...] = (
        "run.terminal",
    )


class WebhookUpdate(Toggle):
    client_ids: list[Identifier] | None = Field(default=None, max_length=100)


class ItemView(Contract):
    item_id: Identifier
    event_id: str
    state: str
    state_label: str
    run_id: str | None
    error: dict[str, Any] | None
    revision: int


class BatchView(Contract):
    batch_id: Identifier
    name: str
    state_label: str
    revision: int
    items: list[ItemView]
