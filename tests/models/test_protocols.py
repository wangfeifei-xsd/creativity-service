"""MOD-A02/A04/A05 协议夹具；通过真实 LiteLLM 转换，但不访问真实供应商。"""

import asyncio
import json
from datetime import timedelta

import httpx
import pytest
from pydantic import SecretBytes

from creativity_service.core.context import AuthContext, Scope
from creativity_service.core.contracts import Attempt, BudgetReservation
from creativity_service.core.primitives import ServiceError, utcnow
from creativity_service.core.security.outbound import ValidatedTarget
from creativity_service.integrations.models.adapter import LiteLLMAdapter
from creativity_service.integrations.models.connection import ModelConnectionClient
from creativity_service.integrations.models.contracts import Cancellation, ModelRequest
from creativity_service.integrations.models.transport import (
    ModelTransport,
    PinnedBackend,
    RawCapture,
)
from creativity_service.integrations.models.usage import normalize_usage
from creativity_service.modules.models.policy import (
    attempt_order,
    configuration_digest,
    may_retry,
    require_capabilities,
    validate_parameters,
)
from creativity_service.modules.models.schemas import FrozenModel, RetryPolicy


def fixture_config(protocol="chat_completions"):
    return FrozenModel(
        scope=Scope(channel_id="one", environment="test"),
        model_id="m1",
        model_version_id="v1",
        model_revision=1,
        model_name="测试模型",
        connection_id="c1",
        connection_version_id="cv1",
        connection_revision=1,
        provider_credential_id="credential1",
        protocol=protocol,
        endpoint="https://models.example/v1",
        provider_model_name="fixture-model",
        timeout_seconds=3,
        parameters={"max_tokens": 100},
        parameter_allowlist=["max_tokens"],
        config_digest="a" * 64,
    )


def fixture_attempt(config):
    return Attempt(
        scope=config.scope,
        attempt_id="attempt1",
        run_id="run1",
        step_id="step1",
        kind="model",
        target_version_id="v1",
        source_request_id=None,
        state="STARTED",
        started_at=utcnow(),
        finished_at=None,
        error=None,
    )


def fixture_reservation(config):
    return BudgetReservation(
        scope=config.scope,
        attempt_id="attempt1",
        run_id="run1",
        reservation_id="reserve1",
        policy_id="budget1",
        reserved=None,
        token_limit=200,
        state="HELD",
        expires_at=utcnow() + timedelta(minutes=1),
    )


class Credentials:
    async def call(self, context, identifier, purpose, operation):
        return await operation(SecretBytes(b"fixture-secret-never-return"))


async def resolved(host, port):
    return ["198.18.1.151"]


async def unchanged(context, frozen, capabilities, *, debug=False):
    return frozen


def adapter(handler):
    return LiteLLMAdapter(
        ModelConnectionClient(Credentials(), resolved, lambda: httpx.MockTransport(handler)),
        unchanged,
    )


def context(config):
    return AuthContext(
        scope=config.scope,
        principal_type="management",
        principal_id="user",
        actor_id="user",
        request_id="request1",
    )


@pytest.mark.parametrize("protocol", ["chat_completions", "anthropic_messages"])
async def test_protocol_text_and_raw_usage(protocol):
    config = fixture_config(protocol)
    seen = []

    async def handler(request):
        seen.append(request)
        if protocol == "chat_completions":
            body = {
                "id": "response-openai",
                "object": "chat.completion",
                "created": 1,
                "model": "fixture-model",
                "choices": [
                    {
                        "index": 0,
                        "message": {"role": "assistant", "content": "验证完成"},
                        "finish_reason": "stop",
                    }
                ],
                "usage": {
                    "prompt_tokens": 12,
                    "completion_tokens": 3,
                    "total_tokens": 15,
                    "prompt_tokens_details": {"cached_tokens": 4},
                },
            }
        else:
            body = {
                "id": "response-anthropic",
                "type": "message",
                "role": "assistant",
                "model": "fixture-model",
                "content": [{"type": "text", "text": "验证完成"}],
                "stop_reason": "end_turn",
                "stop_sequence": None,
                "usage": {
                    "input_tokens": 8,
                    "output_tokens": 3,
                    "cache_read_input_tokens": 4,
                    "cache_creation_input_tokens": 2,
                },
            }
        return httpx.Response(200, json=body, headers={"request-id": "supplier-request"})

    events = [
        e
        async for e in adapter(handler).events(
            context(config),
            config,
            ModelRequest(messages=[{"role": "user", "content": "测试"}]),
            fixture_attempt(config),
            fixture_reservation(config),
        )
    ]
    assert events[-1].kind == "completed", events
    assert [e.text for e in events if e.kind == "text"] == ["验证完成"]
    usage = next(e.usage for e in events if e.kind == "usage")
    assert usage.source_request_id == "supplier-request"
    assert (
        usage.raw_usage[
            "output_tokens" if protocol == "anthropic_messages" else "completion_tokens"
        ]
        == 3
    )
    assert usage.normalized_tokens["input"] == (14 if protocol == "anthropic_messages" else 12)
    assert usage.subset_relations["cache_read"] == "input"
    assert len(seen) == 1
    assert seen[0].url.path == (
        "/v1/chat/completions" if protocol == "chat_completions" else "/v1/messages"
    )
    assert "fixture-secret" not in str(events)


class BrokenStream(httpx.AsyncByteStream):
    async def __aiter__(self):
        yield (
            b'data: {"id":"response1","object":"chat.completion.chunk","created":1,'
            b'"model":"fixture-model","choices":[{"index":0,"delta":{"content":"first"},'
            b'"finish_reason":null}]}\n\n'
        )
        raise httpx.ReadError("fixture-secret must not leak")


async def test_stream_failure_never_retries_or_splices():
    config, calls = fixture_config(), []

    async def handler(request):
        calls.append(request)
        return httpx.Response(
            200, stream=BrokenStream(), headers={"content-type": "text/event-stream"}
        )

    events = [
        e
        async for e in adapter(handler).events(
            context(config),
            config,
            ModelRequest(messages=[{"role": "user", "content": "测试"}], stream=True),
            fixture_attempt(config),
            fixture_reservation(config),
        )
    ]
    assert [e.text for e in events if e.kind == "text"] == ["first"]
    assert events[-1].kind == "failed" and not events[-1].retryable
    assert len(calls) == 1 and "fixture-secret" not in str(events)
    assert next(e.usage for e in events if e.kind == "usage").status == "MISSING"


@pytest.mark.parametrize(
    "status, code, retryable",
    [
        (401, "MODEL_AUTH_FAILED", False),
        (403, "MODEL_PERMISSION_DENIED", False),
        (400, "MODEL_INPUT_INVALID", False),
        (429, "MODEL_RATE_LIMITED", True),
        (500, "MODEL_PROVIDER_UNAVAILABLE", True),
    ],
)
async def test_failures_have_one_attempt_and_usage(status, code, retryable):
    config, calls = fixture_config(), []

    async def handler(request):
        calls.append(request)
        return httpx.Response(
            status, json={"error": {"message": "fixture-secret", "type": "error"}}
        )

    events = [
        e
        async for e in adapter(handler).events(
            context(config),
            config,
            ModelRequest(messages=[{"role": "user", "content": "测试"}]),
            fixture_attempt(config),
            fixture_reservation(config),
        )
    ]
    assert events[-1].error_code == code and events[-1].retryable == retryable
    assert len(calls) == 1 and len([e for e in events if e.kind == "usage"]) == 1
    assert "fixture-secret" not in str(events)


@pytest.mark.parametrize("output_mode", ["native", "prompt"])
async def test_schema_invalid_and_parameter_not_silently_dropped(output_mode):
    config, calls = fixture_config(), []

    async def handler(request):
        calls.append(request)
        return httpx.Response(
            200,
            json={
                "id": "r",
                "object": "chat.completion",
                "created": 1,
                "model": "fixture-model",
                "choices": [
                    {
                        "index": 0,
                        "message": {"role": "assistant", "content": '{"ok":false}'},
                        "finish_reason": "stop",
                    }
                ],
            },
        )

    run = adapter(handler)
    for request, code in [
        (
            ModelRequest(
                messages=[{"role": "user", "content": "x"}],
                output_schema={
                    "type": "object",
                    "properties": {"ok": {"const": True}},
                    "required": ["ok"],
                },
                output_mode=output_mode,
            ),
            "MODEL_OUTPUT_INVALID",
        ),
        (
            ModelRequest(
                messages=[{"role": "user", "content": "x"}], parameters={"temperature": 1}
            ),
            "MODEL_PARAMETER_UNSUPPORTED",
        ),
    ]:
        events = [
            e
            async for e in run.events(
                context(config),
                config,
                request,
                fixture_attempt(config),
                fixture_reservation(config),
            )
        ]
        assert events[-1].error_code == code and not events[-1].retryable
    assert len(calls) == 1
    assert ("response_format" in json.loads(calls[0].content)) == (output_mode == "native")


async def test_prompt_schema_uses_text_capability_and_validates_json():
    config = fixture_config()
    required = []

    async def prepare(context, frozen, capabilities, *, debug=False):
        required.extend(capabilities)
        assert "structured_output" not in capabilities
        return frozen

    async def handler(request):
        body = json.loads(request.content)
        assert "response_format" not in body
        return httpx.Response(
            200,
            json={
                "id": "r",
                "object": "chat.completion",
                "created": 1,
                "model": "fixture-model",
                "choices": [
                    {
                        "index": 0,
                        "message": {"role": "assistant", "content": '{"ok":true}'},
                        "finish_reason": "stop",
                    }
                ],
            },
        )

    run = adapter(handler)
    run.prepare = prepare
    events = [
        e
        async for e in run.events(
            context(config),
            config,
            ModelRequest(
                messages=[{"role": "user", "content": "请返回 JSON"}],
                output_schema={
                    "type": "object",
                    "properties": {"ok": {"const": True}},
                    "required": ["ok"],
                },
                output_mode="prompt",
            ),
            fixture_attempt(config),
            fixture_reservation(config),
        )
    ]
    assert required and set(required) == {"text"}
    assert next(e for e in events if e.kind == "structured").structured == {"ok": True}
    assert events[-1].kind == "completed"


@pytest.mark.parametrize("stream", [False, True])
@pytest.mark.parametrize("content", ["", '{"ok":', '{"ok":true}'])
async def test_output_length_limit_is_not_schema_failure_or_success(stream, content):
    config = fixture_config()
    usage = {"prompt_tokens": 12, "completion_tokens": 100, "total_tokens": 112}

    async def handler(request):
        body = {
            "id": "truncated-response",
            "object": "chat.completion.chunk" if stream else "chat.completion",
            "created": 1,
            "model": "fixture-model",
            "choices": [
                {
                    "index": 0,
                    "delta" if stream else "message": {"role": "assistant", "content": content},
                    "finish_reason": "length",
                }
            ],
            "usage": usage,
        }
        if stream:

            class Stream(httpx.AsyncByteStream):
                async def __aiter__(self):
                    yield f"data: {json.dumps(body)}\n\ndata: [DONE]\n\n".encode()

            return httpx.Response(
                200,
                stream=Stream(),
                headers={"content-type": "text/event-stream"},
            )
        return httpx.Response(200, json=body)

    events = [
        event
        async for event in adapter(handler).events(
            context(config),
            config,
            ModelRequest(
                messages=[{"role": "user", "content": "返回完整结果"}],
                output_schema={"type": "object", "required": ["ok"]},
                output_mode="prompt",
                stream=stream,
            ),
            fixture_attempt(config),
            fixture_reservation(config),
        )
    ]
    assert events[-1].error_code == "MODEL_OUTPUT_TRUNCATED"
    assert "长度上限" in events[-1].message and not events[-1].retryable
    assert not any(event.kind in {"structured", "completed"} for event in events)
    assert next(event.usage for event in events if event.kind == "usage").raw_usage == usage


async def test_cancellation_closes_inflight_request():
    config, started, closed = fixture_config(), asyncio.Event(), asyncio.Event()

    async def handler(request):
        started.set()
        try:
            await asyncio.Event().wait()
        finally:
            closed.set()

    cancellation = Cancellation()

    async def collect():
        return [
            e
            async for e in adapter(handler).events(
                context(config),
                config,
                ModelRequest(messages=[{"role": "user", "content": "x"}]),
                fixture_attempt(config),
                fixture_reservation(config),
                cancellation,
            )
        ]

    task = asyncio.create_task(collect())
    await asyncio.wait_for(started.wait(), 10)
    cancellation.cancel()
    events = await asyncio.wait_for(task, 5)
    assert closed.is_set() and events[-1].kind == "cancelled"


async def test_pins_ip_blocks_redirects_and_duplicate_http():
    config = fixture_config()
    target = ValidatedTarget(config.endpoint, "models.example", 443, ("198.18.1.151",), {})
    backend = PinnedBackend(target)
    with pytest.raises(ServiceError):
        await backend.connect_tcp("other.example", 443)

    async def redirect(request):
        return httpx.Response(302, headers={"location": "https://evil.example"})

    transport = ModelTransport(target, RawCapture(), httpx.MockTransport(redirect))
    async with httpx.AsyncClient(transport=transport) as client:
        with pytest.raises(ServiceError, match="重定向"):
            await client.get(config.endpoint + "/chat/completions")
        with pytest.raises(ServiceError, match="一次"):
            await client.get(config.endpoint + "/chat/completions")


def test_capability_and_config_invalidation_and_order():
    model = dict(
        connection_id="c",
        provider_model_name="v1",
        context_limit=None,
        parameters={},
        parameter_allowlist=[],
    )
    conn = dict(
        id="c",
        protocol="chat_completions",
        endpoint="https://models.example/v1",
        timeout_seconds=10,
    )
    before = configuration_digest(model, conn)
    evidence = {"tools": {"state": "SUPPORTED", "config_digest": before, "evidence": "live"}}
    require_capabilities(evidence, before, ["tools"])
    for changed in [
        configuration_digest({**model, "provider_model_name": "v2"}, conn),
        configuration_digest(model, {**conn, "endpoint": "https://models2.example/v1"}),
        configuration_digest({**model, "parameters": {"max_tokens": 10}}, conn),
    ]:
        with pytest.raises(ServiceError, match="工具调用"):
            require_capabilities(evidence, changed, ["tools"])
    with pytest.raises(ServiceError):
        require_capabilities(
            {"tools": {**evidence["tools"], "evidence": "fixture"}}, before, ["tools"]
        )
    assert attempt_order(
        ["primary", "fallback"], RetryPolicy(max_attempts=3, retries_per_model=1)
    ) == ("primary", "primary", "fallback")
    assert not may_retry("MODEL_AUTH_FAILED", False) and not may_retry("MODEL_TIMEOUT", True)
    with pytest.raises(ServiceError):
        validate_parameters("anthropic_messages", ["seed"], {"seed": 1})
    assert all(
        v is None
        for v in normalize_usage(fixture_config(), "a", "a", None).normalized_tokens.values()
    )


@pytest.mark.parametrize("protocol", ["chat_completions", "anthropic_messages"])
async def test_tool_call_identity_and_arguments(protocol):
    config = fixture_config(protocol)

    async def handler(request):
        if protocol == "chat_completions":
            body = {
                "id": "response1",
                "object": "chat.completion",
                "created": 1,
                "model": "fixture-model",
                "choices": [
                    {
                        "index": 0,
                        "message": {
                            "role": "assistant",
                            "content": None,
                            "tool_calls": [
                                {
                                    "id": "tool1",
                                    "type": "function",
                                    "function": {"name": "echo", "arguments": '{"text":"ok"}'},
                                }
                            ],
                        },
                        "finish_reason": "tool_calls",
                    }
                ],
                "usage": {"prompt_tokens": 10, "completion_tokens": 4},
            }
        else:
            body = {
                "id": "response1",
                "type": "message",
                "role": "assistant",
                "model": "fixture-model",
                "content": [
                    {"type": "tool_use", "id": "tool1", "name": "echo", "input": {"text": "ok"}}
                ],
                "stop_reason": "tool_use",
                "stop_sequence": None,
                "usage": {"input_tokens": 10, "output_tokens": 4},
            }
        return httpx.Response(200, json=body)

    request = ModelRequest(
        messages=[{"role": "user", "content": "call echo"}],
        tools=[
            {
                "type": "function",
                "function": {
                    "name": "echo",
                    "description": "返回文本",
                    "parameters": {
                        "type": "object",
                        "properties": {"text": {"type": "string"}},
                        "required": ["text"],
                    },
                },
            }
        ],
    )
    events = [
        e
        async for e in adapter(handler).events(
            context(config), config, request, fixture_attempt(config), fixture_reservation(config)
        )
    ]
    assert events[-1].kind == "completed", events
    tool = next(e for e in events if e.kind == "tool")
    assert tool.tool_call_id == "tool1" and tool.tool_name == "echo"
    assert json.loads(tool.arguments_delta) == {"text": "ok"}


async def test_anthropic_stream_preserves_raw_cumulative_usage():
    config = fixture_config("anthropic_messages")
    payloads = [
        {
            "type": "message_start",
            "message": {
                "id": "anthropic-response",
                "type": "message",
                "role": "assistant",
                "model": "fixture-model",
                "content": [],
                "stop_reason": None,
                "stop_sequence": None,
                "usage": {
                    "input_tokens": 4,
                    "output_tokens": 0,
                    "cache_read_input_tokens": 3,
                    "cache_creation_input_tokens": 0,
                },
            },
        },
        {"type": "content_block_start", "index": 0, "content_block": {"type": "text", "text": ""}},
        {
            "type": "content_block_delta",
            "index": 0,
            "delta": {"type": "text_delta", "text": "流式验证"},
        },
        {"type": "content_block_stop", "index": 0},
        {
            "type": "message_delta",
            "delta": {"stop_reason": "end_turn", "stop_sequence": None},
            "usage": {"output_tokens": 2},
        },
        {"type": "message_stop"},
    ]

    class Stream(httpx.AsyncByteStream):
        async def __aiter__(self):
            for value in payloads:
                yield ("event: " + value["type"] + "\ndata: " + json.dumps(value) + "\n\n").encode()

    async def handler(request):
        return httpx.Response(
            200,
            stream=Stream(),
            headers={"content-type": "text/event-stream", "request-id": "anthropic-stream"},
        )

    events = [
        e
        async for e in adapter(handler).events(
            context(config),
            config,
            ModelRequest(messages=[{"role": "user", "content": "测试"}], stream=True),
            fixture_attempt(config),
            fixture_reservation(config),
        )
    ]
    assert events[-1].kind == "completed", events
    assert "".join(e.text or "" for e in events) == "流式验证"
    usage = next(e.usage for e in events if e.kind == "usage")
    assert usage.normalized_tokens == {"input": 7, "output": 2, "cache_read": 3, "cache_write": 0}
    assert usage.raw_usage["input_tokens"] == 4 and usage.raw_usage["output_tokens"] == 2


def test_models_contract_exports_are_current():
    from creativity_service.modules.models.export import export

    export(check=True)


@pytest.mark.parametrize(
    "models,reason",
    [
        ([], "请选择首选模型"),
        (["primary", "primary"], "回退模型不能与首选模型相同"),
        (["primary", "fallback", "fallback"], "回退模型不能重复选择"),
    ],
)
def test_route_candidates_report_specific_invalid_selection(models, reason):
    with pytest.raises(ServiceError, match=reason) as error:
        attempt_order(models, RetryPolicy())
    assert error.value.code == "MODEL_ROUTE_INVALID" and error.value.status == 422


def test_single_model_route_retries_without_duplicate_fallback():
    assert attempt_order(["primary"], RetryPolicy(max_attempts=10, retries_per_model=2)) == (
        "primary",
        "primary",
        "primary",
    )
