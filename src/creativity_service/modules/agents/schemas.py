"""智能体配置、检查结果与不可变执行交接；运行快照不接受 HTTP 正文覆盖。"""

import json
from datetime import datetime
from typing import Any, Literal

from pydantic import AliasChoices, Field

from creativity_service.core.context import Environment, Scope
from creativity_service.core.contracts import DisplayStatus, ResourceVersion, VisibleAction
from creativity_service.core.primitives import Contract, Digest, Identifier, Money, Revision
from creativity_service.modules.memory.schemas import MemoryPolicy

WorkflowType = Literal["structured", "template", "tool_loop", "stateful"]
Purpose = Literal["production", "debug", "evaluation"]


class InputSource(Contract):
    source: Literal["input", "step", "constant"]
    step: Identifier | None = None
    path: str = Field(default="", max_length=256)
    value: Any = None


class AgentStep(Contract):
    key: Identifier
    name: str = Field(min_length=1, max_length=128)
    kind: Literal["model", "tool", "compute"]
    operator: Literal["object", "input", "approval"] | None = None
    dependency: Identifier | None = None
    inputs: dict[str, InputSource] = Field(default_factory=dict, max_length=100)
    input_schema: dict[str, Any]
    output_schema: dict[str, Any]
    timeout_seconds: int = Field(default=30, ge=1, le=3600, strict=True)
    failure_policy: Literal["fail", "partial", "retry"] = "retry"
    max_retries: int = Field(default=2, ge=0, le=2, strict=True)


class AgentCondition(Contract):
    path: str = Field(min_length=1, max_length=256)
    operator: Literal["eq", "ne", "exists"] = "eq"
    value: Any = None


class AgentEdge(Contract):
    source: Identifier
    target: Identifier
    condition: AgentCondition | None = None
    otherwise: bool = False


class AgentLimits(Contract):
    deadline_seconds: int = Field(default=60, ge=1, le=3600, strict=True)
    token_limit: int = Field(default=16000, ge=1, le=1000000, strict=True)
    cost_limit: Money | None = None
    max_model_rounds: int = Field(default=6, ge=1, le=100, strict=True)
    max_tool_calls: int = Field(default=10, ge=0, le=100, strict=True)
    max_iterations: int = Field(default=10, ge=1, le=100, strict=True)
    loop_timeout_seconds: int = Field(default=60, ge=1, le=3600, strict=True)
    output_repair_attempts: int = Field(default=1, ge=0, le=2, strict=True)


class AgentContextPolicy(Contract):
    conversation_enabled: bool = False
    context_limit: int = Field(default=8000, ge=1, le=1000000, strict=True)
    summary_policy: Literal["none", "recent"] = "none"
    memory_policy: MemoryPolicy | None = None


class AgentSkillLoading(Contract):
    version_id: Identifier = Field(
        validation_alias=AliasChoices("skill_id", "version_id"), serialization_alias="skill_id"
    )
    selected: bool = False
    selected_files: tuple[str, ...] = Field(default=(), max_length=128)
    loading_mode: Literal["mandatory", "on_demand"] | None = None
    priority: int | None = Field(default=None, ge=-1000, le=1000, strict=True)
    trigger_reason: str = Field(default="配置选择", min_length=1, max_length=256)


class AgentBindings(Contract):
    embedding_route_version: Identifier | None = Field(
        default=None,
        validation_alias=AliasChoices("embedding_route_id", "embedding_route_version"),
        serialization_alias="embedding_route_id",
    )
    prompt_version: Identifier | None = Field(
        default=None,
        validation_alias=AliasChoices("prompt_id", "prompt_version"),
        serialization_alias="prompt_id",
    )
    model_route_version: Identifier | None = Field(
        default=None,
        validation_alias=AliasChoices("model_route_id", "model_route_version"),
        serialization_alias="model_route_id",
    )
    tool_versions: tuple[Identifier, ...] = Field(
        default=(),
        max_length=64,
        validation_alias=AliasChoices("tool_ids", "tool_versions"),
        serialization_alias="tool_ids",
    )
    skill_versions: tuple[Identifier, ...] = Field(
        default=(),
        max_length=32,
        validation_alias=AliasChoices("skill_ids", "skill_versions"),
        serialization_alias="skill_ids",
    )
    skill_loading: tuple[AgentSkillLoading, ...] = Field(default=(), max_length=32)

    def ids(self) -> list[str]:
        # 同一资源可承担多个用途，依赖清单只保存一次；各用途仍分别校验能力。
        return list(
            dict.fromkeys(
                v
                for v in (
                    self.prompt_version,
                    self.model_route_version,
                    self.embedding_route_version,
                    *self.tool_versions,
                    *self.skill_versions,
                )
                if v is not None
            )
        )


class AgentDefinition(Contract):
    instructions: str = Field(default="", max_length=32000)
    workflow_type: WorkflowType
    entrypoint: Identifier
    input_schema: dict[str, Any]
    output_schema: dict[str, Any]
    start_step: Identifier
    steps: tuple[AgentStep, ...] = Field(min_length=1, max_length=64)
    edges: tuple[AgentEdge, ...] = Field(min_length=1, max_length=256)
    bindings: AgentBindings = AgentBindings()
    limits: AgentLimits = AgentLimits()
    context: AgentContextPolicy = AgentContextPolicy()


class AgentCreate(Contract):
    agent_code: str = Field(pattern=r"^[a-z][a-z0-9_.-]{0,63}$")
    name: str = Field(min_length=1, max_length=128)
    description: str = Field(min_length=1, max_length=4000)
    owner: str = Field(min_length=1, max_length=128)
    definition: AgentDefinition
    version_label: str = Field(default="初始草稿", min_length=1, max_length=64)


class AgentEdit(Contract):
    revision: Revision
    name: str = Field(min_length=1, max_length=128)
    description: str = Field(min_length=1, max_length=4000)
    owner: str = Field(min_length=1, max_length=128)


class AgentVersionCreate(Contract):
    version_label: str = Field(min_length=1, max_length=64)
    base_version_id: Identifier


class AgentVersionEdit(Contract):
    revision: Revision
    definition: AgentDefinition


class AgentValidateInput(Contract):
    revision: Revision
    purpose: Purpose = "debug"
    evaluation_refs: tuple[Identifier, ...] = ()


class AgentIssue(Contract):
    code: str
    message: str
    path: str | None = None


class AgentDependency(Contract):
    description: str = ""
    resource_type: str
    resource_id: str
    version_id: str
    name: str
    version_label: str
    revision: int
    state: DisplayStatus
    content_digest: str
    required_capabilities: list[str] = Field(default_factory=list)


class AgentCheck(Contract):
    key: str
    label: str
    passed: bool
    issues: list[AgentIssue] = Field(default_factory=list)


class AgentValidation(Contract):
    valid: bool
    revision: int
    content_digest: str
    dependencies_digest: str | None
    candidate_digest: str | None
    checks: list[AgentCheck]
    dependencies: list[AgentDependency]


class FrozenExecutionSpec(Contract):
    """规范 JSON 是唯一内容真值；属性每次返回副本，避免浅冻结被嵌套修改绕过。"""

    snapshot_id: Identifier
    scope: Scope
    agent_id: Identifier
    agent_code: str
    agent_name: str
    source_version_id: Identifier
    source_revision: Revision
    purpose: Purpose
    content_digest: Digest
    dependencies_digest: Digest
    candidate_digest: Digest
    payload_json: str
    captured_at: datetime

    @property
    def definition(self) -> AgentDefinition:
        return AgentDefinition.model_validate(json.loads(self.payload_json)["definition"])

    @property
    def versions(self) -> tuple[ResourceVersion, ...]:
        return tuple(
            ResourceVersion.model_validate(v) for v in json.loads(self.payload_json)["versions"]
        )


class AgentTestInput(Contract):
    revision: Revision
    input: dict[str, Any]
    idempotency_key: Identifier


class AgentTestView(Contract):
    run_id: str
    state: DisplayStatus
    result: dict[str, Any] | None = None
    duration_ms: int | None = None
    cost: Money | None = None
    trace_url: str | None = None


class AgentReleaseInput(Contract):
    version_id: Identifier
    revision: Revision
    expected_mapping_revision: Revision | None = None
    environment: Environment
    note: str = Field(min_length=1, max_length=1024)
    operation: Literal["publish", "rollback"] = "publish"
    evaluation_refs: tuple[Identifier, ...] = Field(default=(), max_length=32)


class AgentStateInput(Contract):
    revision: Revision
    operation: Literal["offline", "emergency_stop", "enable"]
    reason: str = Field(min_length=1, max_length=1024)


class AgentView(Contract):
    builtin: bool = False
    agent_id: str
    agent_code: str
    name: str
    description: str
    owner: str
    revision: int
    status: DisplayStatus
    actions: list[VisibleAction]


class AgentVersionView(Contract):
    version_id: str
    version_label: str
    revision: int
    status: DisplayStatus
    definition: AgentDefinition
    content_digest: str
    actions: list[VisibleAction]


class AgentReleaseView(Contract):
    release_id: str
    environment: str
    environment_label: str
    version_id: str
    version_label: str
    operation: str
    operation_label: str
    note: str
    actor_name: str | None
    created_at: datetime
    evidence_refs: list[str]


class AgentDifference(Contract):
    field: str
    label: str
    before: Any
    after: Any


class AgentDetail(Contract):
    agent: AgentView
    versions: list[AgentVersionView]
    release_version_id: str | None
    release_revision: int | None
    releases: list[AgentReleaseView]
    differences: list[AgentDifference]


class AgentList(Contract):
    items: list[AgentView]
    actions: list[VisibleAction]


class AgentTemplate(Contract):
    key: str
    name: str
    workflow_type: WorkflowType
    definition: AgentDefinition


class AgentOptions(Contract):
    templates: list[AgentTemplate]
    legacy_templates: list[AgentTemplate] = Field(default_factory=list)
    dependencies: list[AgentDependency]
    environment: Environment
    environment_label: str
