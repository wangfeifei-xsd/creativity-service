"""LiteLLM 薄适配：一次 Attempt 一次请求，不拥有会话、回退和预算循环。"""

import asyncio
import json
import os
from collections.abc import AsyncIterator, Awaitable, Callable
from typing import Any

import httpx
from jsonschema import Draft202012Validator
from pydantic import SecretBytes

from creativity_service.core.context import AuthContext
from creativity_service.core.contracts import Attempt, BudgetReservation
from creativity_service.core.database import assert_external_io_allowed
from creativity_service.core.primitives import ServiceError
from creativity_service.integrations.models.connection import ModelConnectionClient
from creativity_service.integrations.models.contracts import (
    Cancellation,
    ModelEvent,
    ModelRequest,
    cancellable,
    validate_request,
)
from creativity_service.integrations.models.transport import RawCapture
from creativity_service.integrations.models.usage import normalize_usage
from creativity_service.modules.models.policy import PROTOCOLS, may_retry
from creativity_service.modules.models.schemas import FrozenModel

ERRORS = {
    "MODEL_AUTH_FAILED": "模型鉴权失败",
    "MODEL_PERMISSION_DENIED": "模型访问被拒绝",
    "MODEL_RATE_LIMITED": "模型请求受到限流",
    "MODEL_TIMEOUT": "模型调用超时",
    "MODEL_INPUT_INVALID": "模型请求参数不正确",
    "MODEL_PROVIDER_UNAVAILABLE": "模型服务暂不可用",
    "MODEL_OUTPUT_INVALID": "模型输出不符合结构契约",
    "MODEL_OUTPUT_TRUNCATED": "模型输出达到长度上限，请提高模型的最大输出 Token 数后重试",
    "MODEL_CANCELLED": "模型调用已取消",
}


def error_code(exc: Exception) -> str:
    if isinstance(exc, ServiceError):
        return exc.code
    status = getattr(exc, "status_code", None)
    if status == 401:
        return "MODEL_AUTH_FAILED"
    if status == 403:
        return "MODEL_PERMISSION_DENIED"
    if status == 429:
        return "MODEL_RATE_LIMITED"
    if (
        status == 408
        or isinstance(exc, (TimeoutError, httpx.TimeoutException))
        or "Timeout" in type(exc).__name__
    ):
        return "MODEL_TIMEOUT"
    if status in {400, 404, 413, 422}:
        return "MODEL_INPUT_INVALID"
    if (
        (isinstance(status, int) and status >= 500)
        or isinstance(exc, httpx.TransportError)
        or type(exc).__name__ in {"APIConnectionError", "ServiceUnavailableError"}
    ):
        return "MODEL_PROVIDER_UNAVAILABLE"
    return "MODEL_ADAPTER_ERROR"


class LiteLLMAdapter:
    def __init__(
        self,
        client: ModelConnectionClient,
        prepare: Callable[..., Awaitable[FrozenModel]],
    ) -> None:
        self.client, self.prepare = client, prepare

    async def events(
        self,
        context: AuthContext,
        config: FrozenModel,
        request: ModelRequest,
        attempt: Attempt,
        reservation: BudgetReservation,
        cancellation: Cancellation | None = None,
        *,
        debug: bool = False,
    ) -> AsyncIterator[ModelEvent]:
        assert_external_io_allowed()
        cancel = cancellation or Cancellation()
        queue: asyncio.Queue[ModelEvent | None] = asyncio.Queue(maxsize=16)
        required = [
            "text",
            *(["tools"] if request.tools else []),
            *(["structured_output"] if request.requires_native_output else []),
            *(["streaming"] if request.stream else []),
            *(["vision"] if request.requires_vision else []),
        ]
        if request.operation == "embedding":
            required = ["embedding"]

        async def produce() -> None:
            capture, emitted = RawCapture(), False
            current = config
            try:
                if cancel.event.is_set():
                    raise asyncio.CancelledError
                validate_request(config, request, attempt, reservation)
                if not PROTOCOLS[config.protocol].enabled:
                    raise ServiceError("MODEL_PROTOCOL_DISABLED", "模型协议尚未启用", 422)
                current = await self.prepare(context, config, required, debug=debug)
                if (
                    current.provider_credential_id != config.provider_credential_id
                    or current.connection_version_id != config.connection_version_id
                ):
                    raise ServiceError(
                        "MODEL_CONFIGURATION_STALE", "尝试登记后的连接或凭据已变化", 409
                    )
                parameters = validate_request(current, request, attempt, reservation)

                async def before_send() -> None:
                    # 密钥读取可能等待远端，发送前重新检查模型状态和使用授权。
                    checked = await self.prepare(context, current, required, debug=debug)
                    if (
                        checked.provider_credential_id != current.provider_credential_id
                        or checked.connection_version_id != current.connection_version_id
                    ):
                        raise ServiceError(
                            "MODEL_CONFIGURATION_STALE", "模型凭据已轮换，请重新创建尝试", 409
                        )

                async def invoke(secret: SecretBytes, http_client: httpx.AsyncClient) -> None:
                    nonlocal emitted
                    async for event in self._invoke(
                        current,
                        request,
                        parameters,
                        attempt.attempt_id,
                        secret,
                        http_client,
                        cancel,
                    ):
                        if event.kind in {"text", "tool"}:
                            emitted = True
                        await queue.put(event)

                await self.client.call(context, current, invoke, before_send, capture)
                source_id = capture.request_id or capture.response_id or attempt.attempt_id
                await queue.put(
                    ModelEvent(
                        kind="usage",
                        request_sent=capture.request_sent,
                        attempt_id=attempt.attempt_id,
                        source_request_id=capture.request_id or capture.response_id,
                        usage=normalize_usage(
                            current,
                            attempt.attempt_id,
                            source_id,
                            capture.usage,
                            embedding=request.operation == "embedding",
                        ),
                    )
                )
                await queue.put(
                    ModelEvent(
                        kind="completed",
                        request_sent=capture.request_sent,
                        attempt_id=attempt.attempt_id,
                        source_request_id=capture.request_id or capture.response_id,
                    )
                )
            except asyncio.CancelledError:
                source_id = capture.request_id or capture.response_id or attempt.attempt_id
                await queue.put(
                    ModelEvent(
                        kind="usage",
                        request_sent=capture.request_sent,
                        attempt_id=attempt.attempt_id,
                        source_request_id=capture.request_id or capture.response_id,
                        usage=normalize_usage(
                            current,
                            attempt.attempt_id,
                            source_id,
                            capture.usage,
                            final=False,
                            embedding=request.operation == "embedding",
                        ),
                    )
                )
                await queue.put(
                    ModelEvent(
                        kind="cancelled",
                        request_sent=capture.request_sent,
                        attempt_id=attempt.attempt_id,
                        error_code="MODEL_CANCELLED",
                        message=ERRORS["MODEL_CANCELLED"],
                    )
                )
            except Exception as exc:
                code = error_code(exc)
                source_id = capture.request_id or capture.response_id or attempt.attempt_id
                await queue.put(
                    ModelEvent(
                        kind="usage",
                        request_sent=capture.request_sent,
                        attempt_id=attempt.attempt_id,
                        source_request_id=capture.request_id or capture.response_id,
                        usage=normalize_usage(
                            current,
                            attempt.attempt_id,
                            source_id,
                            capture.usage,
                            final=False,
                            embedding=request.operation == "embedding",
                        ),
                    )
                )
                await queue.put(
                    ModelEvent(
                        kind="failed",
                        request_sent=capture.request_sent,
                        attempt_id=attempt.attempt_id,
                        source_request_id=capture.request_id or capture.response_id,
                        error_code=code,
                        message=ERRORS.get(code, "模型调用未完成"),
                        retryable=may_retry(code, emitted),
                    )
                )
            finally:
                await queue.put(None)

        producer = asyncio.create_task(produce())
        try:
            while (event := await queue.get()) is not None:
                yield event
        finally:
            if not producer.done():
                producer.cancel()
                # 关闭消费者时持续清空队列，避免取消反馈被背压锁住。
                while not producer.done():
                    try:
                        queue.get_nowait()
                    except asyncio.QueueEmpty:
                        pass
                    await asyncio.sleep(0)
            await asyncio.gather(producer, return_exceptions=True)

    async def _invoke(
        self,
        config: FrozenModel,
        request: ModelRequest,
        parameters: dict[str, Any],
        attempt_id: str,
        secret: SecretBytes,
        http_client: httpx.AsyncClient,
        cancellation: Cancellation,
    ) -> AsyncIterator[ModelEvent]:
        # 使用已锁定安装包的本地模型映射，导入时不访问外部价格目录。
        os.environ.setdefault("LITELLM_LOCAL_MODEL_COST_MAP", "True")
        import litellm
        from litellm.llms.custom_httpx.http_handler import AsyncHTTPHandler
        from openai import AsyncOpenAI

        litellm.suppress_debug_info = True
        token = secret.get_secret_value().decode()
        provider = "openai" if config.protocol == "chat_completions" else "anthropic"
        if provider == "openai":
            client: Any = AsyncOpenAI(
                api_key=token, base_url=config.endpoint, http_client=http_client, max_retries=0
            )
            base = config.endpoint
            if request.operation == "embedding":
                from creativity_service.modules.memory.semantic import validate_embeddings

                embedded = await cancellable(
                    client.embeddings.create(
                        model=config.provider_model_name,
                        input=[item["content"] for item in request.messages],
                        encoding_format="float",
                    ),
                    cancellation,
                )
                data = sorted(embedded.data, key=lambda item: item.index)
                if [item.index for item in data] != list(range(len(request.messages))):
                    raise ServiceError("MODEL_OUTPUT_INVALID", "向量响应条目不完整", 502)
                vectors = validate_embeddings(
                    [item.embedding for item in data], len(request.messages)
                )
                yield ModelEvent(
                    kind="structured", attempt_id=attempt_id, structured={"embeddings": vectors}
                )
                return
        else:
            # LiteLLM 要求 AsyncHTTPHandler；只替换其传输，不创建其他联网客户端。
            client = object.__new__(AsyncHTTPHandler)
            client.client = http_client
            client.client_alias = "model-attempt"
            client.timeout = config.timeout_seconds
            base = config.endpoint.removesuffix("/v1")
        options: dict[str, Any] = dict(
            model=config.provider_model_name,
            custom_llm_provider=provider,
            messages=request.messages,
            api_key=token,
            api_base=base,
            client=client,
            timeout=config.timeout_seconds,
            stream=request.stream,
            num_retries=0,
            max_retries=0,
            drop_params=False,
            caching=False,
            mock_response=None,
            metadata={"channel_id": config.scope.channel_id, "attempt_id": attempt_id},
            **parameters,
        )
        options["no-log"] = True
        if request.tools:
            options["tools"] = request.tools
        if request.requires_native_output:
            options["response_format"] = {
                "type": "json_schema",
                "json_schema": {"name": "result", "strict": True, "schema": request.output_schema},
            }
        if request.stream and provider == "openai":
            options["stream_options"] = {"include_usage": True}
        result: Any = await cancellable(litellm.acompletion(**options), cancellation)
        parts: list[str] = []
        finished = False
        truncated = False
        has_tool_calls = False
        if request.stream:
            iterator = result.__aiter__()
            try:
                while True:
                    try:
                        chunk: Any = await cancellable(iterator.__anext__(), cancellation)
                    except StopAsyncIteration:
                        break
                    for choice in chunk.choices:
                        finished = finished or bool(choice.finish_reason)
                        truncated = truncated or choice.finish_reason in {"length", "max_tokens"}
                        delta = choice.delta
                        if delta.content:
                            parts.append(delta.content)
                            yield ModelEvent(kind="text", attempt_id=attempt_id, text=delta.content)
                        for tool in delta.tool_calls or []:
                            has_tool_calls = True
                            yield ModelEvent(
                                kind="tool",
                                attempt_id=attempt_id,
                                tool_index=tool.index,
                                tool_call_id=tool.id,
                                tool_name=tool.function.name if tool.function else None,
                                arguments_delta=tool.function.arguments if tool.function else None,
                            )
            finally:
                closer = getattr(result, "aclose", None)
                if closer:
                    await closer()
            if not finished:
                raise ServiceError("MODEL_PROVIDER_UNAVAILABLE", "模型流在结束事件前中断", 502)
        else:
            truncated = result.choices[0].finish_reason in {"length", "max_tokens"}
            message = result.choices[0].message
            if message.content:
                parts.append(message.content)
                yield ModelEvent(kind="text", attempt_id=attempt_id, text=message.content)
            for index, tool in enumerate(message.tool_calls or []):
                has_tool_calls = True
                yield ModelEvent(
                    kind="tool",
                    attempt_id=attempt_id,
                    tool_index=index,
                    tool_call_id=tool.id,
                    tool_name=tool.function.name,
                    arguments_delta=tool.function.arguments,
                )
        if request.output_schema is not None and not has_tool_calls:
            # 长度上限可能耗在推理或正文中；相同参数再次修复仍会截断，不能误报为契约问题。
            if truncated:
                raise ServiceError("MODEL_OUTPUT_TRUNCATED", ERRORS["MODEL_OUTPUT_TRUNCATED"], 422)
            try:
                structured = json.loads("".join(parts))
                Draft202012Validator(request.output_schema).validate(structured)
            except Exception as exc:
                raise ServiceError(
                    "MODEL_OUTPUT_INVALID", ERRORS["MODEL_OUTPUT_INVALID"], 422
                ) from exc
            yield ModelEvent(kind="structured", attempt_id=attempt_id, structured=structured)
