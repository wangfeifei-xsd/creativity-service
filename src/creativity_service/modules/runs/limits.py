"""运行额度失败说明；历史诊断只使用原运行证据，不读取当前模型参数重算。"""

from typing import Any

from pydantic import ValidationError

from creativity_service.core.contracts import ReleaseSnapshot
from creativity_service.integrations.models.contracts import ModelRequest


def token_limit_message(
    name: str,
    limit: int,
    *,
    occupied: int,
    input_tokens: int,
    output_tokens: int,
    additional: int = 0,
) -> str:
    total = occupied + input_tokens + output_tokens + additional
    return (
        f"本地智能体“{name}”的单次运行 Token 上限为 {limit:,} Token。"
        f"本次输入保守预估 {input_tokens:,}、最大输出预留 {output_tokens:,}"
        + (f"、其他预留 {additional:,}" if additional else "")
        + f"，加上此前已用或预占的 {occupied:,}，合计 {total:,} Token，"
        f"超出上限 {total - limit:,} Token；本次模型请求未发送。"
    )


def historical_token_limit_message(
    row: dict[str, Any],
    snapshot: ReleaseSnapshot | None,
    inputs: list[dict[str, Any]],
    *,
    has_model_attempt: bool,
    content_allowed: bool,
) -> str:
    limit = row["execution_policy"].get("token_limit")
    fallback = (
        f"本次请求在模型调用前被本地智能体“{row['agent_name']}”的单次运行 Token 上限拦截"
        + (f"，本次运行配置上限为 {limit:,} Token。" if limit is not None else "。")
        + (
            "历史记录未保留完整的额度计算明细。"
            if content_allowed
            else "当前权限不允许查看额度计算所需的运行内容。"
        )
    )
    candidates = [
        entry["value"]
        for entry in inputs
        if isinstance(entry["value"], dict) and "request" in entry["value"]
    ]
    # 多轮历史运行可能已有迟报或预占修正，不能用当前账本推断当时占用。
    if snapshot is None or limit is None or has_model_attempt or len(candidates) != 1:
        return fallback
    payload = candidates[0]
    model = next(
        (
            version
            for version in snapshot.versions
            if version.resource_type == "model"
            and version.version_id == payload.get("model_version_id")
        ),
        None,
    )
    if model is None:
        return fallback
    parameters = model.content.get("parameters", {})
    if not isinstance(parameters, dict):
        return fallback
    try:
        request = ModelRequest.model_validate(payload["request"])
        input_tokens = request.input_token_upper_bound()
        output_tokens = request.output_token_upper_bound(parameters)
    except (ValidationError, TypeError, ValueError):
        return fallback
    if input_tokens + output_tokens <= limit:
        return fallback
    return token_limit_message(
        row["agent_name"], limit, occupied=0, input_tokens=input_tokens, output_tokens=output_tokens
    )
