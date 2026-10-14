"""压缩响应经过真实协议适配后仍保留供应商用量与响应标识。"""

import gzip
import json
import zlib

import httpx
import pytest

from creativity_service.integrations.models.contracts import ModelRequest
from tests.models.test_protocols import (
    adapter,
    context,
    fixture_attempt,
    fixture_config,
    fixture_reservation,
)


@pytest.mark.parametrize("encoding", ["gzip", "deflate"])
@pytest.mark.parametrize("stream", [False, True])
async def test_compressed_response_preserves_usage(encoding, stream):
    config = fixture_config()
    usage = {"prompt_tokens": 12, "completion_tokens": 3, "total_tokens": 15}
    base = {"id": "compressed-response", "created": 1, "model": "fixture-model"}
    if stream:
        chunks = [
            {
                **base,
                "object": "chat.completion.chunk",
                "choices": [{"index": 0, "delta": {"content": "正常响应"}, "finish_reason": None}],
            },
            {
                **base,
                "object": "chat.completion.chunk",
                "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}],
            },
            {**base, "object": "chat.completion.chunk", "choices": [], "usage": usage},
        ]
        body = (
            "".join(f"data: {json.dumps(chunk)}\r\n\r\n" for chunk in chunks)
            + "data: [DONE]\r\n\r\n"
        ).encode()
    else:
        body = json.dumps(
            {
                **base,
                "object": "chat.completion",
                "choices": [
                    {
                        "index": 0,
                        "message": {"role": "assistant", "content": "正常响应"},
                        "finish_reason": "stop",
                    }
                ],
                "usage": usage,
            }
        ).encode()
    compressed = gzip.compress(body) if encoding == "gzip" else zlib.compress(body)

    class Stream(httpx.AsyncByteStream):
        async def __aiter__(self):
            # 网络分块刻意切开压缩头、正文与尾部，避免只验证一次性解压。
            for offset in range(0, len(compressed), 7):
                yield compressed[offset : offset + 7]

    async def handler(request):
        return httpx.Response(
            200,
            headers={
                "content-type": "text/event-stream" if stream else "application/json",
                "content-encoding": encoding,
            },
            stream=Stream(),
        )

    events = [
        event
        async for event in adapter(handler).events(
            context(config),
            config,
            ModelRequest(messages=[{"role": "user", "content": "测试用量"}], stream=stream),
            fixture_attempt(config),
            fixture_reservation(config),
        )
    ]
    assert events[-1].kind == "completed", events
    assert "".join(event.text or "" for event in events) == "正常响应"
    (reported,) = [event.usage for event in events if event.kind == "usage"]
    assert reported.status == "REPORTED"
    assert reported.source_request_id == "compressed-response"
    assert reported.raw_usage == usage
    assert reported.normalized_tokens["input"] == 12
    assert reported.normalized_tokens["output"] == 3


@pytest.mark.parametrize(
    "finish_reason,content,code",
    [
        ("stop", '{"ok":true "extra":1}', "MODEL_OUTPUT_INVALID"),
        ("stop", '{"ok":"wrong"}', "MODEL_OUTPUT_INVALID"),
        ("length", '{"ok":', "MODEL_OUTPUT_TRUNCATED"),
    ],
)
@pytest.mark.parametrize("stream", [False, True])
async def test_invalid_output_keeps_final_provider_usage(finish_reason, content, code, stream):
    config = fixture_config()
    raw = {
        "prompt_tokens": 5479,
        "completion_tokens": 2265,
        "total_tokens": 7744,
        "completion_tokens_details": {"reasoning_tokens": 1731},
    }
    base = {"id": "invalid-json", "created": 1, "model": "fixture-model"}

    async def handler(request):
        assert json.loads(request.content)["response_format"] == {"type": "json_object"}
        if stream:
            chunks = [
                {
                    **base,
                    "object": "chat.completion.chunk",
                    "choices": [{"index": 0, "delta": {"content": content}, "finish_reason": None}],
                },
                {
                    **base,
                    "object": "chat.completion.chunk",
                    "choices": [{"index": 0, "delta": {}, "finish_reason": finish_reason}],
                    "usage": raw,
                },
            ]
            body = "".join("data: " + json.dumps(v) + "\n\n" for v in chunks) + "data: [DONE]\n\n"

            class Stream(httpx.AsyncByteStream):
                async def __aiter__(self):
                    yield body.encode()

            return httpx.Response(
                200, stream=Stream(), headers={"content-type": "text/event-stream"}
            )
        return httpx.Response(
            200,
            json={
                **base,
                "object": "chat.completion",
                "choices": [
                    {
                        "index": 0,
                        "message": {"role": "assistant", "content": content},
                        "finish_reason": finish_reason,
                    }
                ],
                "usage": raw,
            },
        )

    events = [
        e
        async for e in adapter(handler).events(
            context(config),
            config,
            ModelRequest(
                messages=[{"role": "user", "content": "返回 JSON 对象"}],
                stream=stream,
                output_mode="json",
                output_schema={
                    "type": "object",
                    "properties": {"ok": {"type": "boolean"}},
                    "required": ["ok"],
                    "additionalProperties": False,
                },
            ),
            fixture_attempt(config),
            fixture_reservation(config),
        )
    ]
    assert events[-1].error_code == code
    usage = next(e.usage for e in events if e.kind == "usage")
    assert usage.final and usage.status == "REPORTED"
    assert usage.normalized_tokens["input"] + usage.normalized_tokens["output"] == 7744
    assert usage.normalized_tokens["reasoning"] == 1731
    assert usage.subset_relations["reasoning"] == "output"


@pytest.mark.parametrize("reasoning", [None, -1, True, "2"])
def test_missing_or_invalid_reasoning_is_not_reported_as_zero(reasoning):
    from creativity_service.integrations.models.usage import normalize_usage

    usage = normalize_usage(
        fixture_config(),
        "a",
        "r",
        {
            "prompt_tokens": 10,
            "completion_tokens": 3,
            "completion_tokens_details": {"reasoning_tokens": reasoning},
        },
    )
    assert usage.normalized_tokens.get("reasoning") is None
    assert "reasoning" not in usage.subset_relations
