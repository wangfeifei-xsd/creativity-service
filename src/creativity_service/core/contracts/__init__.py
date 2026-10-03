"""跨模块共享契约，不导入业务实现。"""

from typing import Literal, Self

from pydantic import AwareDatetime, Field, JsonValue, model_validator

from creativity_service.core.auth.types import IdentitySource
from creativity_service.core.context import (
    AuthContext,
    ChannelState,
    ControlAuthContext,
    ControlScope,
    Scope,
)
from creativity_service.core.primitives import Contract, Digest, Identifier, Money, Revision, digest


class ResourceVersion(Contract):
    channel_id: Identifier
    resource_type: Identifier
    resource_id: Identifier
    version_id: Identifier
    version_label: str
    state: Literal["DRAFT", "PUBLISHED", "RETIRED"]
    draft_revision: Revision | None
    content: dict[str, JsonValue]
    content_digest: Digest
    dependency_version_ids: tuple[Identifier, ...]
    dependencies_digest: Digest
    output_schema: dict[str, JsonValue]

    @model_validator(mode="after")
    def frozen_draft(self) -> Self:
        if (self.state == "DRAFT") != (self.draft_revision is not None):
            raise ValueError("草稿须固定修订号；发布版本不携带草稿修订")
        if self.content_digest != digest(
            {"content": self.content, "output_schema": self.output_schema}
        ):
            raise ValueError("版本内容摘要与固定内容不一致")
        return self


class ReleaseSnapshot(Contract):
    snapshot_id: Identifier
    scope: Scope
    run_id: Identifier
    purpose: Literal["production", "debug", "evaluation"]
    versions: tuple[ResourceVersion, ...]
    dependencies_digest: Digest
    output_schema: dict[str, JsonValue]
    captured_at: AwareDatetime

    @model_validator(mode="after")
    def concrete_versions(self) -> Self:
        if not self.versions:
            raise ValueError("快照必须包含具体版本")
        if any(v.channel_id != self.scope.channel_id for v in self.versions):
            raise ValueError("快照依赖不能跨渠道")
        if self.purpose == "production" and any(v.state != "PUBLISHED" for v in self.versions):
            raise ValueError("正式运行仅使用发布版本")
        return self


class Admission(Contract):
    admission_id: Identifier
    scope: Scope
    run_id: Identifier
    policy_ids: tuple[Identifier, ...]
    state: Literal["HELD", "RELEASED"]
    expires_at: AwareDatetime


class BudgetReservation(Contract):
    reservation_id: Identifier
    scope: Scope
    run_id: Identifier
    attempt_id: Identifier
    policy_id: Identifier
    reserved: Money | None
    token_limit: int | None = Field(ge=0)
    state: Literal["HELD", "PENDING", "SETTLED", "RELEASED"]
    expires_at: AwareDatetime


class RunError(Contract):
    code: str
    message: str
    stage: str
    retryable: bool
    request_id: str


class Attempt(Contract):
    scope: Scope
    attempt_id: Identifier
    run_id: Identifier
    step_id: Identifier
    kind: Literal["model", "tool"]
    target_version_id: Identifier
    source_request_id: str | None
    state: Literal["STARTED", "SUCCEEDED", "FAILED", "UNKNOWN"]
    started_at: AwareDatetime
    finished_at: AwareDatetime | None
    error: RunError | None


class UsageEvent(Contract):
    scope: Scope
    attempt_id: Identifier
    connection_id: Identifier
    source_request_id: str
    event_version: Revision
    status: Literal["REPORTED", "ESTIMATED", "MISSING"]
    raw_usage: dict[str, JsonValue] | None
    normalized_tokens: dict[str, int | None]
    subset_relations: dict[str, str]
    cumulative: bool
    final: bool
    observed_at: AwareDatetime

    @model_validator(mode="after")
    def missing_is_not_zero(self) -> Self:
        if any(v is not None and v < 0 for v in self.normalized_tokens.values()):
            raise ValueError("用量不能为负数")
        if self.status == "MISSING" and any(v is not None for v in self.normalized_tokens.values()):
            raise ValueError("缺失用量不能填入数量")
        return self


class EvidenceLocation(Contract):
    field_path: tuple[str | int, ...] | None
    text_start: int | None = Field(ge=0)
    text_end: int | None = Field(ge=0)

    @model_validator(mode="after")
    def validate_position(self) -> Self:
        if (self.text_start is None) != (self.text_end is None):
            raise ValueError("文本位置必须完整")
        if (
            self.text_start is not None
            and self.text_end is not None
            and self.text_end <= self.text_start
        ):
            raise ValueError("文本结束位置须大于开始位置")
        if not self.field_path and self.text_start is None:
            raise ValueError("证据必须提供字段或文本位置")
        return self


class EvidenceRef(Contract):
    evidence_id: Identifier
    scope: Scope
    source_type: str
    source_id: str
    source_version: str
    observed_at: AwareDatetime
    location: EvidenceLocation
    title: str | None
    authorized_actions: tuple[str, ...]


class ToolResult(Contract):
    scope: Scope
    tool_version_id: Identifier
    source_request_id: str
    source_version: str
    observed_at: AwareDatetime
    data: JsonValue
    evidence_refs: tuple[EvidenceRef, ...]
    warnings: tuple[str, ...]
    cursor: str | None
    has_more: bool
    truncated: bool
    coverage: dict[str, JsonValue]

    @model_validator(mode="after")
    def evidence_scope(self) -> Self:
        if any(e.scope != self.scope for e in self.evidence_refs):
            raise ValueError("工具证据范围必须与结果一致")
        return self


class BusinessResult(Contract):
    schema_version: str
    business_status: Literal["COMPLETED", "NEEDS_INPUT", "NO_MATCH", "INSUFFICIENT_DATA", "PARTIAL"]
    data: dict[str, JsonValue]
    warnings: tuple[str, ...]
    evidence_refs: tuple[EvidenceRef, ...]


class Artifact(Contract):
    artifact_id: Identifier
    scope: Scope
    name: str
    content_type: str
    size_bytes: int = Field(ge=0)
    sha256: Digest
    state: Literal["STAGED", "AVAILABLE", "DELETING", "DELETED"]
    expires_at: AwareDatetime
    download_path: str


RunState = Literal[
    "QUEUED",
    "RUNNING",
    "WAITING_INPUT",
    "WAITING_APPROVAL",
    "CANCEL_REQUESTED",
    "SUCCEEDED",
    "FAILED",
    "CANCELLED",
    "TIMED_OUT",
]


class ResultEnvelope(Contract):
    channel_id: Identifier
    run_id: Identifier
    state: RunState
    state_label: str
    release_snapshot_id: Identifier
    result: BusinessResult | None
    partial_output: JsonValue | None
    error: RunError | None
    usage_summary: dict[str, JsonValue]
    artifacts: tuple[Artifact, ...]

    @model_validator(mode="after")
    def success_is_validated(self) -> Self:
        if self.state == "SUCCEEDED" and (self.result is None or self.error is not None):
            raise ValueError("技术成功必须有正式业务结果且无技术错误")
        if self.state != "SUCCEEDED" and self.result is not None:
            raise ValueError("非成功运行不能提供正式业务结果")
        if self.state in {"FAILED", "TIMED_OUT"} and self.error is None:
            raise ValueError("失败或超时须有错误原因")
        return self


class RunEvent(Contract):
    scope: Scope
    event_id: Identifier
    run_id: Identifier
    sequence: Revision
    event_type: Literal[
        "accepted", "step_started", "text_delta", "tool_status", "result", "error", "completed"
    ]
    payload: JsonValue
    occurred_at: AwareDatetime
    expires_at: AwareDatetime


class EventCursorExpired(Contract):
    code: Literal["EVENTS_EXPIRED"] = "EVENTS_EXPIRED"
    message: str = "事件已过期，请查询运行结果"
    run_id: Identifier
    snapshot_path: str


class DeletionGuardResult(Contract):
    scope: Scope
    target_type: str
    target_id: str
    operation: Literal["read", "write", "restore"]
    checked_at: AwareDatetime
    recovery_id: Identifier
    allowed: Literal[True] = True


class DisplayStatus(Contract):
    value: str
    label: str
    tone: Literal["default", "success", "processing", "warning", "error"]


class VersionOption(Contract):
    version_id: Identifier
    resource_name: str | None
    version_label: str | None
    status: DisplayStatus
    selectable: bool
    unavailable_reason: str | None


class NavigationItem(Contract):
    navigation_key: str
    label: str


class VisibleAction(Contract):
    action_key: str
    label: str


CONTRACTS: tuple[type[Contract], ...] = (
    IdentitySource,
    Scope,
    ControlScope,
    AuthContext,
    ControlAuthContext,
    ChannelState,
    ResourceVersion,
    ReleaseSnapshot,
    Admission,
    BudgetReservation,
    Attempt,
    UsageEvent,
    EvidenceRef,
    ToolResult,
    ResultEnvelope,
    RunEvent,
    EventCursorExpired,
    Artifact,
    DeletionGuardResult,
    DisplayStatus,
    VersionOption,
    NavigationItem,
    VisibleAction,
)
