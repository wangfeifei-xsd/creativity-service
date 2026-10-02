"""工具管理与内部执行契约；运行身份不属于模型参数。"""

from typing import Any, Literal

from pydantic import AwareDatetime, Field

from creativity_service.core.context import Environment, Scope
from creativity_service.core.contracts import (
    DisplayStatus,
    ResourceVersion,
    ToolResult,
    VisibleAction,
)
from creativity_service.core.primitives import Contract, Identifier, Revision

SourceType = Literal["http", "mcp", "builtin"]
EffectType = Literal["READ_ONLY", "IDEMPOTENT_WRITE", "EXTERNAL_WRITE"]


class ToolCreate(Contract):
    tool_code: str = Field(pattern=r"^[a-z][a-z0-9_.-]{0,63}$")
    name: str = Field(min_length=1, max_length=128)
    description: str = Field(min_length=1, max_length=4000)
    source_type: SourceType
    owner: str = Field(min_length=1, max_length=128)


class ToolEdit(Contract):
    revision: Revision
    name: str = Field(min_length=1, max_length=128)
    description: str = Field(min_length=1, max_length=4000)
    owner: str = Field(min_length=1, max_length=128)


class ToolBinding(Contract):
    adapter_key: Identifier
    implementation_version: str = Field(min_length=1, max_length=128)
    connection_id: Identifier | None = None


class RetryPolicy(Contract):
    max_attempts: int = Field(default=1, ge=1, le=3, strict=True)
    delay_ms: int = Field(default=100, ge=0, le=2000, strict=True)


class CachePolicy(Contract):
    ttl_seconds: int = Field(default=0, ge=0, le=3600, strict=True)
    freshness_seconds: int = Field(default=60, ge=1, le=86400, strict=True)
    volatile: bool = False


class SubjectRequirements(Contract):
    required: bool = True
    allowed_types: tuple[str, ...] = ()


class ToolDefinition(Contract):
    input_schema: dict[str, Any]
    output_schema: dict[str, Any]
    model_fields_allowed: tuple[str, ...]
    binding: ToolBinding
    effect_type: EffectType
    required_scopes: tuple[str, ...] = ("run:create",)
    allowed_data_domains: tuple[Identifier, ...]
    environments: tuple[Environment, ...]
    subject_requirements: SubjectRequirements = SubjectRequirements()
    timeout_seconds: int = Field(default=10, ge=1, le=120, strict=True)
    max_result_size: int = Field(default=262144, ge=256, le=2097152, strict=True)
    retry_policy: RetryPolicy = RetryPolicy()
    cache_policy: CachePolicy = CachePolicy()
    idempotency_policy: Literal["none"] = "none"


class ToolVersionCreate(Contract):
    version_label: str = Field(min_length=1, max_length=64)
    definition: ToolDefinition


class ToolVersionEdit(Contract):
    revision: Revision
    definition: ToolDefinition


class ToolRelease(Contract):
    version_id: Identifier
    expected_revision: Revision | None = None
    note: str = Field(default="发布工具版本", min_length=1, max_length=1024)


class ToolRevision(Contract):
    revision: Revision


class ToolTestInput(Contract):
    revision: Revision
    arguments: dict[str, Any]


class ToolTestResult(Contract):
    run_id: Identifier
    state: DisplayStatus
    result: ToolResult | None = None


class ToolTestDescription(Contract):
    version_id: Identifier
    revision: Revision
    input_schema: dict[str, Any]
    trusted_scope: Scope
    principal_name: str
    executable: bool
    unavailable_reason: str | None


class ToolView(Contract):
    tool_id: Identifier
    tool_code: str
    name: str
    description: str
    source_type: SourceType
    source_label: str
    owner: str
    revision: Revision
    status: DisplayStatus
    effect_types: list[EffectType]
    effect_labels: list[str]
    actions: list[VisibleAction]


class ToolList(Contract):
    items: list[ToolView]
    actions: list[VisibleAction]
    referenced_agents: list["ToolReference"] = Field(default_factory=list)


class ToolVersionView(Contract):
    version: ResourceVersion
    revision: Revision
    definition: ToolDefinition
    status: DisplayStatus
    execution_enabled: bool
    unavailable_reason: str | None
    actions: list[VisibleAction]


class ToolReference(Contract):
    resource_name: str | None
    resource_type: str
    version_id: str
    version_label: str


class ToolImpact(Contract):
    tool_id: Identifier
    references: list[ToolReference]
    ongoing_calls: int
    message: str


class ToolDetail(Contract):
    tool: ToolView
    versions: list[ToolVersionView]
    release_version_id: str | None
    release_revision: int | None
    impact: ToolImpact


class ToolCallView(Contract):
    tool_call_id: Identifier
    tool_name: str | None
    version_label: str | None
    run_id: Identifier
    step_id: Identifier
    attempt_id: Identifier | None
    state: DisplayStatus
    args_digest: str
    redacted_arguments: dict[str, Any]
    result_summary: dict[str, Any] | None
    source_request_id: str | None
    latency_ms: int | None
    error: dict[str, Any] | None
    evidence_ids: list[str]
    created_at: AwareDatetime


class ToolExecution(Contract):
    """只供运行服务调用；不会登记为业务 HTTP 入参。"""

    run_id: Identifier
    step_id: Identifier
    tool_version_id: Identifier
    arguments: dict[str, Any]


class RunToolGrant(Contract):
    """由 17 读取持久化运行、Agent 白名单和当前授权后提供。"""

    scope: Scope
    run_id: Identifier
    step_id: Identifier
    agent_version_id: Identifier
    tool_version_ids: frozenset[str]
    allowed_actions: frozenset[str]
    authorization_revision: str
    purpose: Literal["production", "debug", "evaluation"]
    draft_revisions: dict[str, int] = Field(default_factory=dict)


class BindingOption(Contract):
    binding: ToolBinding
    name: str
    source_type: SourceType
    effect_type: EffectType
    effect_label: str
    execution_enabled: bool
    unavailable_reason: str | None
