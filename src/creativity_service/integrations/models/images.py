"""校验内嵌图片，避免协议库另行下载外部地址或读取本地文件。"""

import base64
from io import BytesIO
from typing import Any

from PIL import Image

from creativity_service.core.primitives import ServiceError

MAX_IMAGE_BYTES = 5 * 1024 * 1024
MAX_REQUEST_IMAGE_BYTES = 20 * 1024 * 1024
MAX_IMAGE_PIXELS = 16_000_000
IMAGE_FORMATS = {"image/png": "PNG", "image/jpeg": "JPEG", "image/webp": "WEBP"}


def validate_image_url(value: Any) -> int:
    """只接受大小和真实格式均通过检查的内嵌图片，返回解码字节数。"""
    if not isinstance(value, str) or len(value) > (MAX_IMAGE_BYTES + 2) // 3 * 4 + 64:
        raise ServiceError("MODEL_INPUT_INVALID", "单张图片不得超过 5 MiB", 422)
    header, separator, encoded = value.partition(",")
    media_type = header.removeprefix("data:").removesuffix(";base64")
    if not separator or media_type not in IMAGE_FORMATS or header != f"data:{media_type};base64":
        raise ServiceError("MODEL_INPUT_INVALID", "图片须为 PNG、JPEG 或 WebP 的 Base64 数据", 422)
    try:
        raw = base64.b64decode(encoded, validate=True)
        if not raw or len(raw) > MAX_IMAGE_BYTES:
            raise ValueError
        with Image.open(BytesIO(raw)) as picture:
            if (
                picture.format != IMAGE_FORMATS[media_type]
                or picture.width * picture.height > MAX_IMAGE_PIXELS
                or getattr(picture, "is_animated", False)
            ):
                raise ValueError
            picture.verify()
    except (ValueError, OSError, Image.DecompressionBombError) as exc:
        raise ServiceError("MODEL_INPUT_INVALID", "图片数据无效、格式不符或尺寸超限", 422) from exc
    return len(raw)


def validate_image_messages(messages: list[dict[str, Any]]) -> None:
    count, size = 0, 0
    for message in messages:
        content = message.get("content")
        if content is None or isinstance(content, str):
            continue
        if (
            message.get("role") != "user"
            or not isinstance(content, list)
            or not 1 <= len(content) <= 32
        ):
            raise ServiceError("MODEL_INPUT_INVALID", "图片消息须为用户发送的有效内容块", 422)
        for block in content:
            if not isinstance(block, dict):
                raise ServiceError("MODEL_INPUT_INVALID", "消息内容块格式不正确", 422)
            if block.get("type") == "text":
                if set(block) != {"type", "text"} or not isinstance(block.get("text"), str):
                    raise ServiceError("MODEL_INPUT_INVALID", "文本内容块格式不正确", 422)
            elif block.get("type") == "image_url":
                source = block.get("image_url")
                if (
                    set(block) != {"type", "image_url"}
                    or not isinstance(source, dict)
                    or set(source) - {"url", "detail"}
                    or source.get("detail", "auto") not in ("auto", "low", "high")
                ):
                    raise ServiceError("MODEL_INPUT_INVALID", "图片内容块格式不正确", 422)
                count += 1
                if count > 10:
                    raise ServiceError("MODEL_INPUT_INVALID", "一次请求最多包含 10 张图片", 422)
                size += validate_image_url(source.get("url"))
                if size > MAX_REQUEST_IMAGE_BYTES:
                    raise ServiceError("MODEL_INPUT_INVALID", "图片总大小不得超过 20 MiB", 422)
            else:
                raise ServiceError("MODEL_INPUT_INVALID", "消息内容块类型不受支持", 422)
