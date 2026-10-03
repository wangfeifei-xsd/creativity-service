"""记忆传输契约；主体、来源可信度和确认依据不接受客户端覆盖。"""

from datetime import datetime
from typing import Any, Literal

from pydantic import AwareDatetime, Field, JsonValue

from creativity_service.core.contracts import VisibleAction
from creativity_service.core.primitives import Contract, Identifier, Page, Revision

STATES = {
    "PROPOSED": "待确认",
    "ACTIVE": "生效中",
    "SUPERSEDED": "已替代",
    "EXPIRED": "已过期",
    "REVOKED": "已撤销",
}
TYPES = {"PREFERENCE": "明确偏好", "FACT": "稳定事实", "ARCHIVE": "会话归档"}
REASONS = {
    "CREATED": "明确保存",
    "ARCHIVED": "后台归档",
    "INFERRED": "推断候选",
    "CONFIRMED": "用户确认",
    "CORRECTED": "人工修正",
    "SUPERSEDED": "新依据替代",
    "EXPIRED": "有效期届满",
    "SOURCE_CHANGED": "重新核对来源",
    "SOURCE_REVOKED": "有效来源已失效",
    "FORGOTTEN": "请求遗忘",
    "CONFLICT": "与现有依据冲突，等待确认",
}


class MemoryAttribute(Contract):
    key: Identifier
    label: str = Field(min_length=1, max_length=100)
    memory_type: Literal["PREFERENCE", "FACT"] = "PREFERENCE"
    value_schema: dict[str, Any]


class ConsolidationSettings(Contract):
    enabled: bool = True
    idle_seconds: int = Field(default=1800, ge=60, le=604800, strict=True)
    batch_messages: int = Field(default=20, ge=2, le=100, strict=True)


class MemoryPolicy(Contract):
    allowed_types: list[str] = Field(default_factory=lambda: ["PREFERENCE", "FACT"])
    read_enabled: bool = True
    suggest_enabled: bool = True
    write_mode: Literal["DISABLED", "CANDIDATE", "EXPLICIT"] = "EXPLICIT"
    ttl_seconds: int = Field(default=15552000, ge=60, le=31536000, strict=True)
    max_items: int = Field(default=100, ge=1, le=1000, strict=True)
    retrieval_limit: int = Field(default=10, ge=1, le=100, strict=True)
    failure_mode: Literal["OMIT", "FAIL"] = "OMIT"


class PolicyInput(MemoryPolicy):
    attributes: list[MemoryAttribute] | None = Field(default=None, max_length=100)
    consolidation: ConsolidationSettings | None = None
    revision: int = Field(ge=0, strict=True)


class PolicyView(MemoryPolicy):
    attributes: list[MemoryAttribute]
    consolidation: ConsolidationSettings
    revision: int
    actions: list[VisibleAction]


class MemoryCreate(Contract):
    key: Identifier
    memory_type: str = "PREFERENCE"
    value: JsonValue
    expires_at: AwareDatetime | None = None


class MemoryUpdate(Contract):
    revision: Revision
    value: JsonValue
    expires_at: AwareDatetime | None = None


class MemoryConfirm(Contract):
    revision: Revision


class PreferenceInput(Contract):
    enabled: bool
    revision: int = Field(ge=0, strict=True)


class PreferenceView(Contract):
    enabled: bool
    revision: int
    actions: list[VisibleAction]


class SourceInput(Contract):
    """仅供受控运行端口使用，HTTP 明确保存不接收来源标识。"""

    source_type: Literal["message", "evidence", "memory"]
    source_id: Identifier
    source_version: str = Field(min_length=1, max_length=128)


class CandidateInput(MemoryCreate):
    intent: Literal["TASK_ONLY", "INFERRED", "EXPLICIT"]
    source: SourceInput


class MemorySourceView(Contract):
    name: str
    source_type_label: str
    source_version: str
    observed_at: datetime


class MemoryVersionView(Contract):
    version: int
    status_label: str
    reason: str
    changed_at: datetime


class MemoryView(Contract):
    memory_id: str
    layer: Literal["archive", "profile"]
    layer_label: str
    key: str
    display_name: str
    memory_type: str
    type_label: str
    value: JsonValue
    value_label: str
    status: str
    status_label: str
    confirmed: bool
    expires_at: datetime | None
    revision: int
    version_id: str
    version: int
    subject_name: str | None
    usage_count: int
    created_at: datetime
    sources: list[MemorySourceView]
    actions: list[VisibleAction]


class MemorySubject(Contract):
    anchor_id: str
    label: str


class MemoryList(Page[MemoryView]):
    attributes: list[MemoryAttribute]
    actions: list[VisibleAction]


class MemoryDetail(Contract):
    memory: MemoryView
    versions: list[MemoryVersionView]
    preferences: PreferenceView


class MemoryDeletion(Contract):
    deletion_id: str
    status: str
    status_label: str
    count: int
    requested_at: datetime
    completed_at: datetime | None


class MemoryRef(Contract):
    memory_id: Identifier
    version_id: Identifier


class MemorySelection(Contract):
    """队列与缓存只携带引用；加载时仍须核对运行归属和当前状态。"""

    retrieval_id: Identifier
    refs: list[MemoryRef]
    warnings: list[str]


class LoadedMemory(Contract):
    memory_id: str
    version_id: str
    key: str
    display_name: str
    memory_type: str
    value: JsonValue
    sources: list[MemorySourceView]


class MemoryLoad(Contract):
    items: list[LoadedMemory]
    warnings: list[str]
    required_tool_keys: list[str]


class ConsolidationView(Contract):
    id: str
    revision: int
    conversation_name: str
    state: str
    state_label: str
    attempt: int
    generated_count: int
    generation_run_id: str | None
    created_at: datetime
    updated_at: datetime
    message: str | None
    can_retry: bool
