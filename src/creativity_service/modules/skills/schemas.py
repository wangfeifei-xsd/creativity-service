"""技能管理、可移植依赖与运行加载契约。"""

from typing import Any, Literal

from pydantic import Field

from creativity_service.core.contracts import DisplayStatus, VisibleAction
from creativity_service.core.primitives import Contract, Digest, Identifier, Revision


class SkillVariable(Contract):
    name: str = Field(pattern=r"^[a-zA-Z][a-zA-Z0-9_]{0,63}$")
    label: str = Field(min_length=1, max_length=128)
    value_type: Literal["string", "number", "boolean", "object", "array"] = "string"
    required: bool = True


class SkillToolRequirement(Contract):
    tool_code: str = Field(pattern=r"^[a-z][a-z0-9_.-]{0,63}$")
    version_label: str = Field(default="当前配置", min_length=1, max_length=64)
    source_type: Literal["mcp", "http", "builtin"] | None = None
    input_schema: dict[str, Any] | None = None
    output_schema: dict[str, Any] | None = None


class SkillSettings(Contract):
    change_note: str = Field(default="", max_length=4000)
    tool_requirements: tuple[SkillToolRequirement, ...] = Field(default=(), max_length=32)
    tool_bindings: dict[str, Identifier] = Field(default_factory=dict, max_length=32)
    required_model_capabilities: tuple[str, ...] = Field(default=(), max_length=16)
    input_variables: tuple[SkillVariable, ...] = Field(default=(), max_length=64)
    loading_mode: Literal["mandatory", "on_demand"] = "on_demand"
    priority: int = Field(default=0, ge=-1000, le=1000, strict=True)
    context_budget: int = Field(default=16000, ge=1, le=200000, strict=True)
    allowed_agents: tuple[Identifier, ...] = Field(default=(), max_length=64)
    conflict_groups: tuple[str, ...] = Field(default=(), max_length=32)


class SkillFileInput(Contract):
    relative_path: str = Field(min_length=1, max_length=1024)
    text: str = Field(max_length=1048576)


class SkillCreate(Contract):
    skill_code: str = Field(pattern=r"^[a-z][a-z0-9_.-]{0,63}$")
    name: str = Field(min_length=1, max_length=128)
    description: str = Field(min_length=1, max_length=4000)
    owner: str = Field(min_length=1, max_length=128)
    tags: tuple[str, ...] = Field(default=(), max_length=32)
    instructions: str = Field(min_length=1, max_length=1048576)
    version_label: str = Field(default="当前配置", min_length=1, max_length=64)
    settings: SkillSettings = SkillSettings()


class SkillImport(Contract):
    skill_code: str = Field(pattern=r"^[a-z][a-z0-9_.-]{0,63}$")
    name: str | None = Field(default=None, min_length=1, max_length=128)
    owner: str = Field(min_length=1, max_length=128)
    archive_base64: str = Field(min_length=1, max_length=12000000)
    version_label: str = Field(default="当前配置", min_length=1, max_length=64)
    tool_bindings: dict[str, Identifier] = Field(default_factory=dict, max_length=32)


class SkillImportPreviewInput(Contract):
    archive_base64: str = Field(min_length=1, max_length=12000000)


class SkillRevision(Contract):
    revision: Revision


class SkillEdit(SkillRevision):
    name: str = Field(min_length=1, max_length=128)
    description: str = Field(min_length=1, max_length=4000)
    owner: str = Field(min_length=1, max_length=128)
    tags: tuple[str, ...] = Field(default=(), max_length=32)
    status: Literal["ACTIVE"]


class SkillVersionCreate(Contract):
    version_label: str = Field(default="当前配置", min_length=1, max_length=64)
    base_version_id: Identifier


class SkillVersionEdit(SkillRevision):
    settings: SkillSettings
    files: tuple[SkillFileInput, ...] = Field(default=(), max_length=128)
    remove_paths: tuple[str, ...] = Field(default=(), max_length=128)


class SkillRelease(Contract):
    version_id: Identifier
    expected_revision: Revision | None = None
    note: str = Field(default="发布技能版本", min_length=1, max_length=1024)


class SkillFile(Contract):
    relative_path: str
    content_type: str
    size_bytes: int
    sha256: Digest
    loadable: bool
    unavailable_reason: str | None = None


class SkillImportPreview(Contract):
    metadata: dict[str, Any]
    files: list[SkillFile]
    settings: SkillSettings
    instruction_preview: str


class SkillDefinition(SkillSettings):
    package_hash: Digest
    entry_file: Literal["SKILL.md"] = "SKILL.md"
    metadata: dict[str, Any]
    files: tuple[SkillFile, ...]
    artifact_id: Identifier
    required_tool_versions: tuple[Identifier, ...]


class SkillIssue(Contract):
    code: str
    message: str
    path: str | None = None


class SkillDependency(Contract):
    name: str | None
    version_label: str
    version_id: str | None
    available: bool
    reason: str | None = None


class SkillValidation(Contract):
    valid: bool
    issues: list[SkillIssue]
    dependencies: list[SkillDependency]
    package_hash: Digest


class SkillView(Contract):
    skill_id: Identifier
    skill_code: str
    name: str
    description: str
    owner: str
    tags: list[str]
    revision: Revision
    status: DisplayStatus
    actions: list[VisibleAction]


class SkillList(Contract):
    items: list[SkillView]
    actions: list[VisibleAction]


class SkillVersionSummary(Contract):
    version_id: Identifier
    version_label: str
    revision: Revision
    status: DisplayStatus
    settings: SkillSettings
    metadata: dict[str, Any]
    files: list[SkillFile]
    package_hash: Digest
    discovery_preview: str
    actions: list[VisibleAction]


class SkillVersionView(SkillVersionSummary):
    instruction_preview: str


class SkillReference(Contract):
    version_id: Identifier
    resource_name: str | None
    version_label: str
    status: DisplayStatus


class SkillDetail(Contract):
    skill: SkillView
    versions: list[SkillVersionSummary]
    references: list[SkillReference]
    release_version_id: str | None
    release_revision: int | None


class SkillBinding(Contract):
    version_id: Identifier
    selected: bool = False
    selected_files: tuple[str, ...] = Field(default=(), max_length=128)
    trigger_reason: str = Field(default="按需选择", min_length=1, max_length=256)
    variables: dict[str, Any] = Field(default_factory=dict)
    loading_mode: Literal["mandatory", "on_demand"] | None = None
    priority: int | None = Field(default=None, ge=-1000, le=1000, strict=True)


class SkillLoadRequest(Contract):
    """由 16/17 验证 Agent、模型与工具授权后构造，不能直接作为业务 API 正文。"""

    bindings: tuple[SkillBinding, ...] = Field(max_length=32)
    agent_id: Identifier | None = None
    authorized_tool_versions: tuple[Identifier, ...] = ()
    model_capabilities: tuple[str, ...] = ()
    context_budget: int = Field(default=32000, ge=1, le=200000, strict=True)
    purpose: Literal["runtime", "test"] = "runtime"


class SkillLoadedFile(Contract):
    version_id: Identifier
    path: str
    sha256: Digest
    text: str
    budget_units: int
    trigger_reason: str


class SkillOmission(Contract):
    version_id: Identifier
    path: str
    reason: str


class SkillDiscovery(Contract):
    version_id: Identifier
    name: str
    description: str
    text: str


class SkillLoadResult(Contract):
    loaded: list[SkillLoadedFile]
    discoveries: list[SkillDiscovery]
    omitted: list[SkillOmission]
    issues: list[SkillIssue]
    used_budget: int
    budget_unit: Literal["utf8_bytes"] = "utf8_bytes"
    callable_tool_versions: list[str]
    complete: bool


class SkillDependencySummary(Contract):
    channel_id: Identifier
    skill_id: Identifier
    version_id: Identifier
    content_digest: Digest
    package_hash: Digest
    dependencies_digest: Digest
    required_tool_versions: tuple[Identifier, ...]
    required_model_capabilities: tuple[str, ...]


class SkillTestInput(SkillRevision):
    selected: bool = True
    selected_files: tuple[str, ...] = Field(default=(), max_length=128)
    variables: dict[str, Any] = Field(default_factory=dict)
    context_budget: int = Field(default=32000, ge=1, le=200000, strict=True)
    execute_scripts: bool = False


class SkillTestView(Contract):
    test_id: Identifier
    version_id: Identifier
    version_label: str
    created_at: str
    result: SkillLoadResult
    run_id: str | None = None
    evidence_label: str = "加载验证"


class SkillFileContent(Contract):
    path: str
    text: str | None
    unavailable_reason: str | None


class SkillToolOption(SkillToolRequirement):
    version_id: Identifier
    name: str
    available: bool
    reason: str | None


class SkillAgentOption(Contract):
    agent_id: Identifier
    name: str
