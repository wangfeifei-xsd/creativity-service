"""MCP 受信身份与结果元数据契约；领域数据始终保持远端结构。"""

from datetime import timedelta
from typing import Any, Literal

from mcp.types import CallToolResult
from pydantic import AwareDatetime, Field, ValidationError

from creativity_service.core.context import AuthContext, Scope
from creativity_service.core.primitives import Contract, ServiceError, utcnow
from creativity_service.integrations.tools import (
    AdapterRequest,
    AdapterResult,
    SourceEvidence,
    ToolAdapterError,
)
from creativity_service.modules.iam.authorization import IamAuthorization


class McpIdentity(Contract):
    protocol: Literal["creativity.mcp-identity.v1"] = "creativity.mcp-identity.v1"
    channel_id: str
    environment: str
    subject_type: str | None
    subject_id: str | None
    principal_id: str
    client_id: str | None
    key_id: str | None
    actions: list[str]
    resources: dict[str, list[str]]
    request_id: str
    run_id: str | None
    attempt_id: str
    observed_at: AwareDatetime
    expires_at: AwareDatetime
    operation: dict[str, str] | None = None


class McpResultMetadata(Contract):
    protocol: Literal["creativity.tool-result.v1"]
    scope: Scope
    source_request_id: str = Field(min_length=1, max_length=256)
    source_version: str = Field(min_length=1, max_length=128)
    observed_at: AwareDatetime
    actual_effect: Literal["READ_ONLY", "IDEMPOTENT_WRITE", "EXTERNAL_WRITE"]
    result_status: Literal["complete", "empty", "missing", "partial"]
    evidence: tuple[SourceEvidence, ...] = Field(default=(), max_length=100)
    warnings: tuple[str, ...] = ()
    cursor: str | None = None
    has_more: bool = False
    truncated: bool = False
    coverage: dict[str, Any] = Field(default_factory=dict)
    error_category: Literal["forbidden", "timeout", "unavailable", "invalid", "remote"] | None = (
        None
    )


def wrap_result(result: CallToolResult, request: AdapterRequest, schema_hash: str) -> AdapterResult:
    metadata = (result.meta or {}).get("creativity.result")
    value = None
    if metadata is not None:
        try:
            value = McpResultMetadata.model_validate(metadata)
        except ValidationError:
            raise ServiceError("TOOL_RESULT_INVALID", "远端结果元数据不符合协议", 502) from None
        if value.scope != request.context.scope or any(
            e.scope != value.scope for e in value.evidence
        ):
            raise ServiceError("TOOL_RESULT_INVALID", "远端结果或证据超出受信范围", 502)
        if value.actual_effect != request.definition.effect_type:
            raise ServiceError("TOOL_EFFECT_MISMATCH", "远端报告的实际副作用与固定契约不符", 502)
    if result.isError:
        code = {
            "forbidden": "MCP_REMOTE_FORBIDDEN",
            "timeout": "MCP_REMOTE_TIMEOUT",
            "unavailable": "MCP_UNAVAILABLE",
            "invalid": "MCP_RESULT_INVALID",
        }.get(value.error_category or "remote" if value else "remote", "MCP_REMOTE_ERROR")
        raise ToolAdapterError(
            code,
            "远端工具拒绝或未能完成调用",
            source_request_id=value.source_request_id if value else None,
        )
    if value and value.error_category:
        raise ServiceError("TOOL_RESULT_INVALID", "远端成功状态与错误类别冲突", 502)
    if any(item.type != "text" for item in result.content):
        raise ServiceError("TOOL_RESULT_INVALID", "远端文件或媒体引用未经平台授权", 502)
    if request.definition.subject_requirements.required and (
        value is None or result.structuredContent is None
    ):
        raise ServiceError("TOOL_RESULT_INVALID", "业务工具须返回结构化结果和受信范围元数据", 502)
    data = (
        result.structuredContent
        if result.structuredContent is not None
        else {
            "content": [item.model_dump(mode="json", exclude_none=True) for item in result.content]
        }
    )
    if value is None:
        # 无主体的旧工具保留原始文本/结构；来源时间明确是平台接收时间。
        return AdapterResult(
            data=data,
            source_request_id=request.attempt_id,
            source_version=schema_hash,
            observed_at=utcnow(),
            coverage={"observation_source": "platform_receipt"},
        )
    if (
        value.result_status == "partial" or value.has_more or value.truncated
    ) and not value.coverage:
        raise ServiceError("TOOL_RESULT_INVALID", "部分结果须明确适用范围", 502)
    if (value.has_more or value.truncated) and value.result_status != "partial":
        raise ServiceError("TOOL_RESULT_INVALID", "结果完整性与分页或截断状态冲突", 502)
    return AdapterResult(
        data=data,
        source_request_id=value.source_request_id,
        source_version=value.source_version,
        observed_at=value.observed_at,
        source_evidence=value.evidence,
        warnings=value.warnings,
        cursor=value.cursor,
        has_more=value.has_more,
        truncated=value.truncated,
        coverage={**value.coverage, "result_status": value.result_status},
    )


async def trusted_identity(
    authorization: IamAuthorization, context: AuthContext, request: AdapterRequest, tool_id: str
) -> dict[str, Any]:
    """仅发送当前工具的授权，不把服务凭据或请求 Token 写入主体信息。"""
    decision = await authorization.check(context, "run:create", "tool", tool_id)
    if not decision.allowed or not set(request.definition.required_scopes) <= set(decision.actions):
        raise ServiceError("TOOL_FORBIDDEN", "工具当前权限已撤销", 403)
    resources = {"tool": [tool_id]}
    expires_at = utcnow() + timedelta(seconds=30)
    if not context.actor_id:
        if authorization.subjects is None:
            raise ServiceError("SUBJECT_REVIEW_REQUIRED", "缺少当前主体复核", 403)
        subject = await authorization.subjects.read_current(context)
        if (
            subject.scope != context.scope
            or not (set(request.definition.required_scopes) | {"run:create"})
            <= subject.actions & subject.agent_actions
            or not {tool_id, "*"} & subject.resources.get("tool", frozenset())
        ):
            raise ServiceError("TOOL_FORBIDDEN", "工具当前主体权限已撤销", 403)
        resources = {k: sorted(v) for k, v in subject.resources.items()}
        resources["tool"] = [tool_id]
        expires_at = min(expires_at, subject.expires_at)
    return McpIdentity(
        **context.scope.model_dump(),
        principal_id=context.principal_id,
        client_id=context.client_id,
        key_id=context.key_id,
        actions=sorted(set(request.definition.required_scopes) | {"run:create"}),
        resources=resources,
        request_id=context.request_id,
        run_id=request.run_id,
        attempt_id=request.attempt_id,
        observed_at=utcnow(),
        expires_at=expires_at,
        operation=request.operation,
    ).model_dump(mode="json")
