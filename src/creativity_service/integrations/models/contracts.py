"""供应商无关的一次尝试请求、流事件和取消信号。"""

import asyncio
from collections.abc import Awaitable
from typing import Any, Literal

from pydantic import Field

from creativity_service.core.contracts import Attempt, BudgetReservation, UsageEvent
from creativity_service.core.primitives import Contract, ServiceError, utcnow
from creativity_service.core.versioning import validate_schema
from creativity_service.modules.models.policy import validate_parameters
from creativity_service.modules.models.schemas import FrozenModel


class ModelRequest(Contract):
    messages: list[dict[str, Any]] = Field(min_length=1, max_length=1000)
    tools: list[dict[str, Any]] = Field(default_factory=list, max_length=100)
    output_schema: dict[str, Any] | None = None
    parameters: dict[str, Any] = Field(default_factory=dict)
    stream: bool = False


class ModelEvent(Contract):
    kind: Literal["text", "tool", "usage", "structured", "completed", "failed", "cancelled"]
    attempt_id: str
    text: str | None = None
    tool_index: int | None = None
    tool_call_id: str | None = None
    tool_name: str | None = None
    arguments_delta: str | None = None
    structured: Any = None
    usage: UsageEvent | None = None
    source_request_id: str | None = None
    error_code: str | None = None
    message: str | None = None
    retryable: bool = False
    request_sent: bool = False


class Cancellation:
    def __init__(self) -> None:
        self.event = asyncio.Event()

    def cancel(self) -> None:
        self.event.set()


async def cancellable[T](awaitable: Awaitable[T], cancellation: Cancellation) -> T:
    task = asyncio.ensure_future(awaitable)
    cancelled = asyncio.create_task(cancellation.event.wait())
    try:
        done, _ = await asyncio.wait([task, cancelled], return_when=asyncio.FIRST_COMPLETED)
        if cancelled in done:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
            raise asyncio.CancelledError
        return await task
    finally:
        if not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        cancelled.cancel()
        await asyncio.gather(cancelled, return_exceptions=True)


def validate_request(
    config: FrozenModel, request: ModelRequest, attempt: Attempt, reservation: BudgetReservation
) -> dict[str, Any]:
    if (
        attempt.scope != config.scope
        or reservation.scope != config.scope
        or attempt.kind != "model"
        or attempt.state != "STARTED"
        or reservation.state != "HELD"
        or reservation.expires_at <= utcnow()
        or attempt.attempt_id != reservation.attempt_id
        or attempt.run_id != reservation.run_id
        or attempt.target_version_id != config.model_version_id
    ):
        raise ServiceError(
            "MODEL_ATTEMPT_REQUIRED", "模型调用需要匹配的执行尝试和有效预算预占", 403
        )
    parameters = {**config.parameters, **request.parameters}
    validate_parameters(config.protocol, config.parameter_allowlist, parameters)
    if request.output_schema is not None:
        validate_schema(request.output_schema)
    for message in request.messages:
        if message.get("role") not in {"system", "user", "assistant", "tool"} or set(message) - {
            "role",
            "content",
            "tool_call_id",
            "tool_calls",
            "name",
        }:
            raise ServiceError("MODEL_INPUT_INVALID", "消息格式不受支持", 422)
        if message.get("content") is not None and not isinstance(message["content"], str):
            raise ServiceError("MODEL_CAPABILITY_UNSUPPORTED", "当前适配器仅支持文本内容", 422)
    for tool in request.tools:
        if tool.get("type") != "function" or not isinstance(tool.get("function"), dict):
            raise ServiceError("MODEL_INPUT_INVALID", "工具定义格式不正确", 422)
        validate_schema(tool["function"].get("parameters", {}))
    if request.output_schema is not None and config.protocol == "anthropic_messages":
        # 首批 Messages 不模拟原生 schema 保证，能力测试会明确失败。
        raise ServiceError(
            "MODEL_CAPABILITY_UNSUPPORTED", "当前 Messages 适配器未启用结构化输出", 422
        )
    if "max_tokens" not in parameters:
        raise ServiceError("MODEL_PARAMETER_INVALID", "调用必须显式设置最大输出 Token 数", 422)
    if reservation.token_limit is not None and parameters["max_tokens"] > reservation.token_limit:
        raise ServiceError("MODEL_BUDGET_EXCEEDED", "最大输出 Token 数超出本次预占额度", 422)
    return parameters
