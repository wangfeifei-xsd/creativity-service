"""视觉协议转换、图片边界和识别判定；不将协议夹具视为真实能力证据。"""

import base64
import json
from io import BytesIO

import httpx
import pytest
from PIL import Image

from creativity_service.core.primitives import ServiceError
from creativity_service.integrations.models.contracts import ModelRequest
from creativity_service.integrations.models.images import (
    validate_image_messages,
    validate_image_url,
)
from creativity_service.modules.models.schemas import TestInput as CasesInput
from creativity_service.modules.models.testing import CASES
from creativity_service.modules.models.vision import vision_case
from tests.models.test_protocols import (
    adapter,
    context,
    fixture_attempt,
    fixture_config,
    fixture_reservation,
)


def image_content(url):
    return [{"type": "image_url", "image_url": {"url": url}}]


@pytest.mark.parametrize("protocol", ["chat_completions", "anthropic_messages"])
@pytest.mark.parametrize("correct", [True, False])
async def test_vision_protocol_sends_image_without_answer_or_native_schema(protocol, correct):
    config = fixture_config(protocol)
    case = vision_case(CASES["vision"])
    content = [{"type": "text", "text": case.prompt}, *image_content(case.images[0])]
    seen, required = [], []

    async def prepare(context, frozen, capabilities, *, debug=False):
        required.append(capabilities)
        return frozen

    async def handler(request):
        body = json.loads(request.content)
        seen.append(body)
        assert "response_format" not in body
        assert "output_config" not in body
        assert body["messages"][0]["content"][0] == content[0]
        sent_image = body["messages"][0]["content"][1]
        if protocol == "chat_completions":
            assert sent_image == content[1]
        else:
            assert sent_image == {
                "type": "image",
                "source": {
                    "type": "base64",
                    "media_type": "image/png",
                    "data": case.images[0].split(",", 1)[1],
                },
            }
        answer = json.dumps(case.output_schema["const"] if correct else {"shapes": []})
        if protocol == "chat_completions":
            response = {
                "id": "vision1",
                "object": "chat.completion",
                "created": 1,
                "model": "fixture-model",
                "choices": [
                    {
                        "index": 0,
                        "message": {"role": "assistant", "content": answer},
                        "finish_reason": "stop",
                    }
                ],
                "usage": {"prompt_tokens": 100, "completion_tokens": 40},
            }
        else:
            response = {
                "id": "vision1",
                "type": "message",
                "role": "assistant",
                "model": "fixture-model",
                "content": [{"type": "text", "text": answer}],
                "stop_reason": "end_turn",
                "stop_sequence": None,
                "usage": {"input_tokens": 100, "output_tokens": 40},
            }
        return httpx.Response(200, json=response)

    run = adapter(handler)
    run.prepare = prepare
    events = [
        event
        async for event in run.events(
            context(config),
            config,
            ModelRequest(
                messages=[{"role": "user", "content": content}],
                output_schema=case.output_schema,
                output_mode=case.output_mode,
            ),
            fixture_attempt(config),
            fixture_reservation(config),
            debug=True,
        )
    ]
    assert len(seen) == 1
    assert required and all(set(value) == {"text", "vision"} for value in required)
    assert len([event for event in events if event.kind == "usage"]) == 1
    assert events[-1].kind == ("completed" if correct else "failed")
    if not correct:
        assert events[-1].error_code == "MODEL_OUTPUT_INVALID"
    else:
        assert (
            next(event.structured for event in events if event.kind == "structured")
            == (case.output_schema["const"])
        )


@pytest.mark.parametrize(
    "url",
    [
        "https://images.example/test.png",
        "file:///tmp/image.png",
        "data:image/svg+xml;base64,PHN2Zz4=",
        "data:image/png;base64,!!!",
        "data:image/png;base64,dGV4dA==",
        "data:image/png;base64,",
    ],
)
async def test_invalid_image_never_calls_provider(url):
    config = fixture_config()
    calls = []

    async def handler(request):
        calls.append(request)
        raise AssertionError("无效图片不得发送给供应商")

    events = [
        event
        async for event in adapter(handler).events(
            context(config),
            config,
            ModelRequest(messages=[{"role": "user", "content": image_content(url)}]),
            fixture_attempt(config),
            fixture_reservation(config),
        )
    ]
    assert not calls
    assert events[-1].error_code == "MODEL_INPUT_INVALID"


def test_image_format_size_and_message_boundaries(monkeypatch):
    case = vision_case(CASES["vision"])
    url = case.images[0]
    assert validate_image_url(url) > 0
    assert CasesInput().cases == ["text", "usage"]
    assert len(CasesInput(cases=list(CASES)).cases) == 7
    for invalid in [
        {"role": "assistant", "content": image_content(url)},
        {"role": "user", "content": []},
        {"role": "user", "content": [{"type": "text", "text": 1}]},
        {"role": "user", "content": [{"type": "audio", "data": "x"}]},
        {"role": "user", "content": image_content(url) * 11},
    ]:
        with pytest.raises(ServiceError):
            validate_image_messages([invalid])
    with pytest.raises(ServiceError):
        validate_image_url(url.replace("image/png", "image/jpeg"))
    monkeypatch.setattr("creativity_service.integrations.models.images.MAX_IMAGE_PIXELS", 10)
    with pytest.raises(ServiceError):
        validate_image_url(url)
    monkeypatch.setattr(
        "creativity_service.integrations.models.images.MAX_IMAGE_PIXELS", 16_000_000
    )
    monkeypatch.setattr("creativity_service.integrations.models.images.MAX_REQUEST_IMAGE_BYTES", 10)
    with pytest.raises(ServiceError):
        validate_image_messages([{"role": "user", "content": image_content(url)}])
    monkeypatch.setattr("creativity_service.integrations.models.images.MAX_IMAGE_BYTES", 10)
    with pytest.raises(ServiceError):
        validate_image_url(url)


@pytest.mark.parametrize("format, media_type", [("PNG", "png"), ("JPEG", "jpeg"), ("WEBP", "webp")])
def test_supported_image_formats(format, media_type):
    buffer = BytesIO()
    Image.new("RGB", (32, 32), "red").save(buffer, format=format)
    url = f"data:image/{media_type};base64," + base64.b64encode(buffer.getvalue()).decode()
    assert validate_image_url(url) == len(buffer.getvalue())
