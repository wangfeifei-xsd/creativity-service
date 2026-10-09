"""能力、参数和固定回退顺序的确定性业务规则。"""

from typing import Any
from urllib.parse import unquote, urlsplit

from creativity_service.core.primitives import ServiceError, digest
from creativity_service.modules.models.schemas import (
    Capability,
    ProtocolType,
    ProtocolView,
    RetryPolicy,
    TestCase,
)

PROTOCOLS: dict[str, ProtocolView] = {
    "chat_completions": ProtocolView(
        code="chat_completions",
        name="Chat Completions 兼容",
        enabled=True,
        reason=None,
        parameters=["max_tokens", "temperature", "top_p", "stop", "seed"],
    ),
    "anthropic_messages": ProtocolView(
        code="anthropic_messages",
        name="Anthropic Messages",
        enabled=True,
        reason=None,
        parameters=["max_tokens", "temperature", "top_p", "stop"],
    ),
    "responses": ProtocolView(
        code="responses",
        name="OpenAI Responses",
        enabled=False,
        reason="协议适配尚未启用",
        parameters=[],
    ),
    "gemini_generate_content": ProtocolView(
        code="gemini_generate_content",
        name="Gemini generateContent",
        enabled=False,
        reason="协议适配尚未启用",
        parameters=[],
    ),
}
CAPABILITY_NAMES = {
    "text": "文本生成",
    "tools": "工具调用",
    "structured_output": "原生结构化输出",
    "streaming": "流式输出",
    "vision": "视觉理解",
    "embedding": "向量生成",
}
CAPABILITY_LABELS = {"SUPPORTED": "已支持", "UNSUPPORTED": "不支持", "UNVERIFIED": "未验证"}
HEALTH_LABELS = {"UNKNOWN": "未知", "HEALTHY": "正常", "DEGRADED": "异常", "UNAVAILABLE": "不可用"}
CASE_CAPABILITIES: dict[TestCase, Capability] = {
    "embedding": "embedding",
    "text": "text",
    "schema": "structured_output",
    "tools": "tools",
    "stream_cancel": "streaming",
}
RECOVERABLE = frozenset({"MODEL_TIMEOUT", "MODEL_RATE_LIMITED", "MODEL_PROVIDER_UNAVAILABLE"})


def validate_endpoint(protocol: ProtocolType, endpoint: str) -> str:
    try:
        parsed = urlsplit(endpoint)
        hostname = (parsed.hostname or "").encode("idna").decode()
        port = parsed.port
    except (ValueError, UnicodeError) as exc:
        raise ServiceError("MODEL_ENDPOINT_INVALID", "模型基础地址格式不正确", 422) from exc
    if parsed.scheme != "https" or not hostname or port == 0:
        raise ServiceError("MODEL_ENDPOINT_INVALID", "模型基础地址必须是有效的 HTTPS 地址", 422)
    if parsed.query or parsed.fragment or parsed.username or parsed.password or not parsed.hostname:
        raise ServiceError("MODEL_ENDPOINT_INVALID", "模型地址不能包含凭据、查询参数或片段", 422)
    if (
        "\\" in endpoint
        or any(ord(char) < 33 or ord(char) == 127 for char in endpoint)
        or any(part in {".", ".."} for part in unquote(parsed.path).split("/"))
    ):
        raise ServiceError("MODEL_ENDPOINT_INVALID", "模型基础地址格式不正确", 422)
    path = parsed.path.rstrip("/")
    if any(
        part in path
        for part in ("/responses", "/chat/completions", "/messages", ":generateContent")
    ):
        raise ServiceError("MODEL_ENDPOINT_INVALID", "请填写协议基础地址，不包含调用接口路径", 422)
    if protocol == "anthropic_messages" and path not in ("", "/v1"):
        raise ServiceError("MODEL_ENDPOINT_INVALID", "Messages 基础地址应为站点地址或 /v1", 422)
    return endpoint.rstrip("/")


def validate_parameters(
    protocol: ProtocolType, allowed: list[str], parameters: dict[str, Any]
) -> None:
    supported = set(PROTOCOLS[protocol].parameters)
    invalid = set(allowed) - supported | (parameters.keys() - set(allowed))
    if len(set(allowed)) != len(allowed) or invalid:
        raise ServiceError(
            "MODEL_PARAMETER_UNSUPPORTED",
            "参数未获协议或模型支持：" + "、".join(sorted(invalid)),
            422,
        )
    for key, value in parameters.items():
        valid = False
        if key == "max_tokens":
            valid = type(value) is int and 1 <= value <= 1000000
        elif key in {"temperature", "top_p"}:
            maximum = 2 if key == "temperature" and protocol == "chat_completions" else 1
            valid = type(value) in {int, float} and 0 <= value <= maximum
        elif key == "seed":
            valid = type(value) is int and -(2**31) <= value < 2**31
        elif key == "stop":
            valid = (
                isinstance(value, list)
                and 1 <= len(value) <= 4
                and all(isinstance(v, str) and 0 < len(v) <= 256 for v in value)
            )
        if not valid:
            raise ServiceError("MODEL_PARAMETER_INVALID", f"参数 {key} 的值不正确", 422)


def configuration_digest(model: dict[str, Any], connection: dict[str, Any]) -> str:
    # 凭据轮换不改变能力语义，但冻结快照仍记录实际使用的凭据版本。
    return digest(
        {
            "model_validation_revision": model.get("validation_revision", 1),
            "connection_validation_revision": connection.get("validation_revision", 1),
            "connection": {
                **{k: connection[k] for k in ("id", "protocol", "endpoint", "timeout_seconds")},
            },
            "model": {
                k: model[k]
                for k in (
                    "connection_id",
                    "provider_model_name",
                    "context_limit",
                    "parameters",
                    "parameter_allowlist",
                )
            },
        }
    )


def require_capabilities(evidence: dict[str, Any], config_digest: str, required: list[str]) -> None:
    failed = [
        CAPABILITY_NAMES[c]
        for c in required
        if evidence.get(c, {}).get("state") != "SUPPORTED"
        or evidence.get(c, {}).get("config_digest") != config_digest
        or evidence.get(c, {}).get("evidence") != "live"
    ]
    if failed:
        raise ServiceError(
            "MODEL_CAPABILITY_UNSUPPORTED",
            "以下能力尚未通过当前配置验证：" + "、".join(failed),
            422,
        )


def attempt_order(models: list[str], policy: RetryPolicy) -> tuple[str, ...]:
    if not models:
        raise ServiceError("MODEL_ROUTE_INVALID", "请选择首选模型", 422)
    if models[0] in models[1:]:
        raise ServiceError(
            "MODEL_ROUTE_INVALID",
            "回退模型不能与首选模型相同；如需重试，请设置每个模型重试上限",
            422,
        )
    if len(set(models)) != len(models):
        raise ServiceError("MODEL_ROUTE_INVALID", "回退模型不能重复选择", 422)
    return tuple(model for model in models for _ in range(policy.retries_per_model + 1))[
        : policy.max_attempts
    ]


def may_retry(error_code: str, emitted_output: bool) -> bool:
    # 任意正文或工具增量交付后均终止本流；运行时另起尝试必须显式区分。
    return not emitted_output and error_code in RECOVERABLE
