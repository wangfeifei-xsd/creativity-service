"""模型错误只提取诊断文本，移除凭据和请求正文后交给运行记录。"""

import json
import re
from typing import Any

from creativity_service.core.primitives import ServiceError


def safe_message(value: str, sensitive: list[str]) -> str:
    # 先脱敏再截断，防止被截断的密钥前缀残留；同时覆盖 JSON 转义后的正文。
    for secret in sorted(set(sensitive), key=len, reverse=True):
        if secret:
            if len(secret) < 4 and secret.isascii():
                value = re.sub(rf"(?<!\w){re.escape(secret)}(?!\w)", "[已脱敏]", value)
            else:
                value = value.replace(secret, "[已脱敏]")
            escaped = json.dumps(secret, ensure_ascii=True)[1:-1]
            if escaped != secret:
                value = value.replace(escaped, "[已脱敏]")
    value = re.sub(r"(?i)\b(?:bearer|basic)\s+[^\s,;\"']+", "[凭据已脱敏]", value)
    value = re.sub(r"\bsk-[A-Za-z0-9_-]+", "[密钥已脱敏]", value)
    value = re.sub(
        r"(?i)(api[_-]?key|authorization|password|access[_-]?token|secret)"
        r"[\"']?\s*[:=]\s*(?:\"[^\"]*\"|'[^']*'|[^\s,;&}]+)",
        r"\1=[已脱敏]",
        value,
    )
    value = re.sub(r"https?://[^\s<>\"']+", "[地址已省略]", value)
    value = " ".join(value.split())
    return value[:1000] + ("…" if len(value) > 1000 else "")


def request_texts(value: Any) -> list[str]:
    """请求可能包含文本、工具参数或图片地址，均不应由异常再次回显。"""
    if isinstance(value, str):
        return [value] if value else []
    if isinstance(value, dict):
        return [text for item in value.values() for text in request_texts(item)]
    if isinstance(value, list):
        return [text for item in value for text in request_texts(item)]
    return []


def failure_message(
    exc: Exception,
    summary: str,
    *,
    provider_error: str | None,
    status: int | None,
    request_sent: bool,
    sensitive: list[str],
) -> str:
    if isinstance(exc, ServiceError):
        return safe_message(exc.message, sensitive)
    # SDK 的异常字符串常拼接整份请求；优先使用响应中的独立错误字段。
    detail = provider_error
    if not detail:
        body = getattr(exc, "body", None)
        if isinstance(body, dict):
            error = body.get("error", body)
            if isinstance(error, dict) and isinstance(error.get("message"), str):
                detail = error["message"]
    if not detail:
        # 只展示本地异常或最底层网络异常的一行原因，拒绝 SDK 拼接的大段正文。
        cause: BaseException = exc
        seen = {id(cause)}
        while cause.__cause__ is not None and id(cause.__cause__) not in seen:
            cause = cause.__cause__
            seen.add(id(cause))
        sdk_message = getattr(exc, "message", None)
        if type(cause).__module__.split(".")[0] in {"builtins", "httpx", "httpcore", "ssl"}:
            detail = (
                f"{type(cause).__name__}: {str(cause).splitlines()[0]}"
                if str(cause)
                else type(cause).__name__
            )
        elif (
            not request_sent
            and type(exc).__module__.split(".")[0]
            in {
                "litellm",
                "openai",
                "anthropic",
            }
            and isinstance(sdk_message, str)
        ):
            # SDK 在发出请求前也会拒绝参数；保留独立说明，剔除附带请求结构和堆栈。
            detail = sdk_message.splitlines()[0] if sdk_message else type(exc).__name__
            detail = re.split(
                r"(?i)(?:request(?:_body)?|headers|messages|input)[\"']?\s*[:=]\s*[\[{]",
                detail,
                maxsplit=1,
            )[0]
        else:
            detail = f"{type(exc).__name__}；上游未提供可展示的具体原因"
    safe = safe_message(detail, sensitive)
    http = f"（HTTP {status}）" if status is not None else ""
    return f"{summary}{http}：{safe}"
