"""保留原协议计量；缺失不补零，缓存子集关系显式交给 08。"""

from typing import Any

from creativity_service.core.contracts import UsageEvent
from creativity_service.core.primitives import utcnow
from creativity_service.modules.models.schemas import FrozenModel


def count(value: Any) -> int | None:
    return value if type(value) is int and value >= 0 else None


def normalize_usage(
    config: FrozenModel,
    attempt_id: str,
    request_id: str,
    raw: dict[str, Any] | None,
    *,
    event_version: int = 1,
    final: bool = True,
) -> UsageEvent:
    tokens: dict[str, int | None] = {
        "input": None,
        "output": None,
        "cache_read": None,
        "cache_write": None,
    }
    subsets: dict[str, str] = {}
    if raw is not None:
        if config.protocol == "chat_completions":
            details = raw.get("prompt_tokens_details") or {}
            tokens.update(
                input=count(raw.get("prompt_tokens")),
                output=count(raw.get("completion_tokens")),
                cache_read=count(details.get("cached_tokens")),
            )
            subsets["cache_read"] = "input"
        elif config.protocol == "anthropic_messages":
            # Messages 的 input_tokens 不包含缓存读写，先合为总输入再标明子集。
            base, read, write = (
                count(raw.get(k))
                for k in ("input_tokens", "cache_read_input_tokens", "cache_creation_input_tokens")
            )
            tokens.update(
                input=(base + (read or 0) + (write or 0)) if base is not None else None,
                output=count(raw.get("output_tokens")),
                cache_read=read,
                cache_write=write,
            )
            subsets.update(cache_read="input", cache_write="input")
    return UsageEvent(
        scope=config.scope,
        attempt_id=attempt_id,
        connection_id=config.connection_id,
        source_request_id=request_id,
        event_version=event_version,
        status="REPORTED" if raw is not None else "MISSING",
        raw_usage=raw,
        normalized_tokens=tokens,
        subset_relations=subsets,
        cumulative=True,
        final=final,
        observed_at=utcnow(),
    )
