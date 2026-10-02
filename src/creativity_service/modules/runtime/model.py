"""一次外部模型调用对应一个运行尝试和账本预占，适配器不拥有重试循环。"""

import asyncio
from contextlib import suppress
from typing import Any

from jsonschema import Draft202012Validator

from creativity_service.core.context import AuthContext
from creativity_service.core.contracts import Attempt, BudgetReservation
from creativity_service.core.primitives import ServiceError, canonical_json, utcnow
from creativity_service.integrations.models.contracts import Cancellation, ModelRequest
from creativity_service.modules.agents.schemas import FrozenExecutionSpec
from creativity_service.modules.models.assembly import ModelServices
from creativity_service.modules.models.schemas import FrozenModel
from creativity_service.modules.runs.repositories import rows
from creativity_service.modules.runs.schemas import Lease
from creativity_service.modules.runs.services import RunService
from creativity_service.modules.runtime.storage import record_inputs
from creativity_service.modules.usage.schemas import AttemptPlan


def plan_for(
    spec: FrozenExecutionSpec, model: FrozenModel, request: ModelRequest, run_id: str
) -> AttemptPlan:
    # UTF-8 字节数作为保守上界，包含工具及结果结构，不能用缺失的实际用量代替估算。
    input_tokens = len(canonical_json(request.model_dump(mode="json")))
    return AttemptPlan(
        run_id=run_id,
        attempt_id="pending",
        agent_id=spec.agent_id,
        model_id=model.model_id,
        connection_id=model.connection_id,
        purpose=spec.purpose,
        input_tokens=input_tokens,
        max_output_tokens=int(
            request.parameters.get("max_tokens", model.parameters.get("max_tokens", 1024))
        ),
        source_type="run",
        names={"agent": spec.agent_name, "model": model.model_name},
    )


class ModelRunner:
    def __init__(self, runs: RunService, models: ModelServices) -> None:
        self.runs, self.models = runs, models

    async def invoke(
        self,
        context: AuthContext,
        lease: Lease,
        spec: FrozenExecutionSpec,
        node_key: str,
        request: ModelRequest,
        candidates: list[FrozenModel],
        order: list[str],
        *,
        capability_test: bool = False,
        cancel_after_chunks: int | None = None,
    ) -> dict[str, Any]:
        progress = await self.runs.load_progress(lease, node_key)
        if progress and progress["output"] is not None:
            # finish_attempt 与响应同事务保存，崩溃发生在步骤提交前也可恢复。
            await self.runs.commit_step(lease, node_key, progress["output"])
            return dict(progress["output"])
        started = await self.runs.start_step(lease, node_key, request.model_dump(mode="json"))
        if started is None:
            raise ServiceError("RUN_INACTIVE", "运行已停止", 409)
        policy = self.runs.step_policy(await self.runs.before_progress(lease), node_key)
        async with self.runs.engine.connect() as connection:
            previous = sorted(
                await rows(connection, "attempts", context.scope.channel_id, step_id=started["id"]),
                key=lambda a: a["created_at"],
            )
        repairs = sum(
            a["retryable"] and (a["error"] or {}).get("code") == "MODEL_OUTPUT_INVALID"
            for a in previous
        )
        number = int(started["attempt_count"])
        last: ServiceError = ServiceError("MODEL_CALL_LIMIT", "模型尝试次数已达上限", 429)
        if previous and previous[-1]["error"]:
            last = ServiceError(
                previous[-1]["error"]["code"], previous[-1]["error"]["message"], 502
            )
            if not previous[-1]["retryable"]:
                raise last
            if last.code == "MODEL_OUTPUT_INVALID":
                request = request.model_copy(
                    update={
                        "messages": [
                            *request.messages,
                            {
                                "role": "user",
                                "content": "上次输出结构无效，请按指定 JSON 结构修复。",
                            },
                        ]
                    }
                )
        while number < policy.max_retries + 1:
            route_index = number - repairs
            if route_index >= len(order):
                break
            model_id = order[route_index]
            config = next(m for m in candidates if m.model_id == model_id)
            required = [
                "text",
                *(["tools"] if request.tools else []),
                *(["structured_output"] if request.output_schema is not None else []),
                *(["streaming"] if request.stream else []),
            ]
            config = await self.models.routing.prepare_attempt(
                context, config, required, debug=capability_test
            )
            row = await self.runs.before_progress(lease)
            if row["state"] != "RUNNING":
                raise ServiceError("RUN_INACTIVE", "运行已停止", 409)
            config = config.model_copy(
                update={
                    "timeout_seconds": max(
                        1,
                        min(
                            config.timeout_seconds,
                            int((row["deadline"] - utcnow()).total_seconds()),
                        ),
                    )
                }
            )
            plan = plan_for(spec, config, request, lease.run_id)
            await record_inputs(
                self.runs,
                lease,
                f"{node_key}.attempt{number}",
                {
                    "request": request.model_dump(mode="json"),
                    "model_name": config.model_name,
                    "model_version_id": config.model_version_id,
                },
                [],
            )
            stored = await self.runs.start_attempt(
                lease,
                node_key,
                plan,
                target_version_id=config.model_version_id,
                provider_credential_id=config.provider_credential_id,
            )
            if stored is None:
                raise ServiceError("RUN_INACTIVE", "运行已停止", 409)
            attempt = Attempt(
                scope=context.scope,
                attempt_id=stored["id"],
                run_id=lease.run_id,
                step_id=stored["step_id"],
                kind="model",
                target_version_id=config.model_version_id,
                source_request_id=None,
                state="STARTED",
                started_at=stored["started_at"],
                finished_at=None,
                error=None,
            )
            reservation = BudgetReservation(
                reservation_id=stored["usage_id"],
                scope=context.scope,
                run_id=lease.run_id,
                attempt_id=attempt.attempt_id,
                policy_id="run-budget",
                reserved=None,
                token_limit=plan.input_tokens + plan.max_output_tokens,
                state="HELD",
                expires_at=row["deadline"],
            )
            if not await self.runs.mark_sent(lease, attempt.attempt_id):
                raise ServiceError("RUN_INACTIVE", "运行已停止", 409)
            cancel = Cancellation()

            async def monitor(signal: Cancellation) -> None:
                try:
                    while True:
                        await asyncio.sleep(0.25)
                        current = await self.runs.before_progress(lease)
                        if current["state"] != "RUNNING" or current["deadline"] <= utcnow():
                            signal.cancel()
                            return
                except Exception:
                    signal.cancel()

            watcher = asyncio.create_task(monitor(cancel))
            text_parts: list[str] = []
            tool_parts: dict[int, dict[str, str]] = {}
            structured: Any = None
            failure: ServiceError | None = None
            retryable = False
            reported = False
            chunks = 0
            source_id: str | None = None
            cancelled = False
            complete = False
            content_error: ServiceError | None = None

            async def consume(
                config: FrozenModel,
                request: ModelRequest,
                attempt: Attempt,
                reservation: BudgetReservation,
                cancel: Cancellation,
                text_parts: list[str],
                tool_parts: dict[int, dict[str, str]],
            ) -> None:
                nonlocal \
                    source_id, \
                    reported, \
                    chunks, \
                    structured, \
                    cancelled, \
                    failure, \
                    retryable, \
                    complete, \
                    content_error
                async for event in self.models.adapter.events(
                    context, config, request, attempt, reservation, cancel, debug=capability_test
                ):
                    source_id = event.source_request_id or source_id
                    if event.kind == "usage" and event.usage is not None:
                        # 用量独立于内容提交；取消、删除和过期租约之后仍按原渠道结算。
                        await self.runs.ledger.settle(event.usage)
                        reported = event.usage.status == "REPORTED" and event.usage.final
                    elif event.kind == "text" and content_error is None:
                        text_parts.append(event.text or "")
                        chunks += 1
                        if request.stream:
                            try:
                                await self.runs.append_event(
                                    lease,
                                    "text_delta",
                                    {
                                        "text": event.text,
                                        "attempt_id": attempt.attempt_id,
                                        "label": "部分内容",
                                        "validated": False,
                                    },
                                )
                            except ServiceError as exc:
                                # 内容已删除或执行权失效时停止正文提交，仍消费取消尾部的真实用量。
                                content_error = exc
                                cancel.cancel()
                        if cancel_after_chunks and chunks >= cancel_after_chunks:
                            cancel.cancel()
                    elif event.kind == "structured":
                        structured = event.structured
                    elif event.kind == "tool":
                        part = tool_parts.setdefault(
                            event.tool_index or 0, {"id": "", "name": "", "arguments": ""}
                        )
                        part["id"] += event.tool_call_id or ""
                        part["name"] += event.tool_name or ""
                        part["arguments"] += event.arguments_delta or ""
                    elif event.kind in {"failed", "cancelled"}:
                        cancelled = event.kind == "cancelled"
                        failure = ServiceError(
                            event.error_code or "MODEL_FAILED", event.message or "模型调用失败", 502
                        )
                        retryable = event.retryable and not (request.stream and text_parts)
                    elif event.kind == "completed":
                        complete = True

            consumer = asyncio.create_task(
                consume(config, request, attempt, reservation, cancel, text_parts, tool_parts)
            )
            try:
                await asyncio.shield(consumer)
            except asyncio.CancelledError:
                cancel.cancel()
                try:
                    await asyncio.wait_for(asyncio.shield(consumer), timeout=5)
                except (TimeoutError, ServiceError):
                    consumer.cancel()
                    with suppress(asyncio.CancelledError):
                        await consumer
                raise
            finally:
                watcher.cancel()
                with suppress(asyncio.CancelledError):
                    await watcher
            if content_error:
                raise content_error
            output = {
                "text": "".join(text_parts),
                "structured": structured,
                "tools": list(tool_parts.values()),
                "attempt_id": attempt.attempt_id,
                "reported": reported,
                "cancelled": cancelled,
                "chunks": chunks,
            }
            expected_cancel = capability_test and cancel_after_chunks and cancelled and chunks > 0
            if failure is None and not complete:
                failure = ServiceError(
                    "MODEL_RESULT_UNKNOWN", "模型未确认请求完成，用量待核实", 502
                )
            if (
                failure is None
                and request.output_schema is not None
                and not tool_parts
                and not Draft202012Validator(request.output_schema).is_valid(structured)
            ):
                failure = ServiceError("MODEL_OUTPUT_INVALID", "模型输出未通过结构校验", 422)
            if failure and not expected_cancel:
                repair = (
                    failure.code == "MODEL_OUTPUT_INVALID"
                    and not request.stream
                    and repairs < spec.definition.limits.output_repair_attempts
                )
                if repair:
                    repairs += 1
                    retryable = True
                    request = request.model_copy(
                        update={
                            "messages": [
                                *request.messages,
                                {
                                    "role": "user",
                                    "content": "上次输出结构无效，请按指定 JSON 结构修复。",
                                },
                            ]
                        }
                    )
                await self.runs.finish_attempt(
                    lease,
                    attempt.attempt_id,
                    "UNKNOWN" if failure.code == "MODEL_RESULT_UNKNOWN" else "FAILED",
                    source_request_id=source_id,
                    retryable=retryable,
                    error=self.runs.error(row, failure.code, failure.message),
                )
                last = failure
                if not retryable:
                    raise failure
                number += 1
                continue
            if not await self.runs.finish_attempt(
                lease, attempt.attempt_id, "SUCCEEDED", source_request_id=source_id, output=output
            ):
                raise ServiceError("RUN_INACTIVE", "运行已停止", 409)
            await self.runs.commit_step(lease, node_key, output)
            return output
        raise last
