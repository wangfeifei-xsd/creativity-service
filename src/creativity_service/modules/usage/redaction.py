"""用量账本仅保留供应商计量数字，不接收响应原文或任意诊断字符串。"""

from typing import Any

TOKEN_FIELDS = {
    "prompt_tokens",
    "completion_tokens",
    "input_tokens",
    "output_tokens",
    "total_tokens",
    "cached_tokens",
    "reasoning_tokens",
    "audio_tokens",
    "image_tokens",
    "accepted_prediction_tokens",
    "rejected_prediction_tokens",
    "cache_creation_input_tokens",
    "cache_read_input_tokens",
    "input_tokens_details",
    "output_tokens_details",
    "prompt_tokens_details",
    "completion_tokens_details",
}


def meter_only(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {}
    return {
        key: meter_only(item) if isinstance(item, dict) else item
        for key, item in value.items()
        if key in TOKEN_FIELDS
        and (type(item) in {int, float} or item is None or isinstance(item, dict))
    }
