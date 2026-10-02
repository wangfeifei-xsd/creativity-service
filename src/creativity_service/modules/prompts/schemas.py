"""提示词传输与运行交接契约；渠道和平台变量不接受正文指定。"""

from datetime import datetime
from typing import Annotated, Any, Literal

from pydantic import Field, JsonValue

from creativity_service.core.context import Scope
from creativity_service.core.contracts import DisplayStatus, ResourceVersion, VisibleAction
from creativity_service.core.primitives import Contract, Revision

VariableSource = Literal["input", "tool", "memory", "platform"]
VariableType = Literal["string", "integer", "number", "boolean", "object", "array"]
Sensitivity = Literal["public", "internal", "sensitive", "secret"]
Name = Annotated[str, Field(min_length=1, max_length=128)]


class PromptVariable(Contract):
    name: str = Field(min_length=1, max_length=128)
    display_name: Name
    type: VariableType
    required: bool = True
    default: JsonValue = None
    max_length: int = Field(default=4096, ge=1, le=100000)
    source: VariableSource = "input"
    sensitivity: Sensitivity = "internal"


class InstructionBlocks(Contract):
    system: str = Field(default="", max_length=100000)
    output_requirements: str = Field(default="", max_length=100000)


class MessageTemplate(Contract):
    source: VariableSource
    template: str = Field(max_length=100000)


class PromptContent(Contract):
    instruction_blocks: InstructionBlocks = Field(default_factory=InstructionBlocks)
    message_templates: list[MessageTemplate] = Field(default_factory=list, max_length=100)
    variables: list[PromptVariable] = Field(default_factory=list, max_length=100)
    change_note: str = Field(default="", max_length=2000)


class PromptCreate(Contract):
    prompt_code: str = Field(min_length=1, max_length=64)
    name: Name
    purpose: str = Field(min_length=1, max_length=512)


class PromptUpdate(Contract):
    revision: Revision
    name: Name
    purpose: str = Field(min_length=1, max_length=512)


class PromptDraftCreate(Contract):
    version_label: str = Field(min_length=1, max_length=64)
    content: PromptContent


class PromptDraftEdit(Contract):
    revision: Revision
    content: PromptContent


class PromptRenderRequest(Contract):
    input: dict[str, JsonValue] = Field(default_factory=dict)
    sample_id: str | None = None
    max_preview_chars: int = Field(default=12000, ge=128, le=100000)
    reveal_sensitive: bool = False


class RenderedSection(Contract):
    source: Literal["system", "output", "input", "tool", "memory", "platform"]
    label: str
    text: str
    original_characters: int
    truncated_characters: int


class PromptRenderView(Contract):
    sections: list[RenderedSection]
    estimated_tokens: int
    estimate_method: str = "按 UTF-8 字节数估算，实际用量以模型记录为准"
    context_limit: int | None = None
    estimated_remaining_tokens: int | None = None
    masked: bool


class PromptVersionView(Contract):
    version: ResourceVersion
    name: str
    revision: int
    status: DisplayStatus
    actions: list[VisibleAction]


class PromptReleaseRequest(Contract):
    version_id: str
    revision: Revision
    expected_mapping_revision: Revision | None = None
    note: str = Field(min_length=1, max_length=2000)
    operation: Literal["publish", "rollback"] = "publish"


class PromptReleaseView(Contract):
    environment: str
    environment_label: str
    version_id: str
    version_label: str
    revision: int
    published_at: datetime


class PromptView(Contract):
    prompt_id: str
    prompt_code: str
    name: str
    purpose: str
    revision: int
    version_label: str | None
    status: DisplayStatus
    releases: list[PromptReleaseView]
    last_test_at: datetime | None
    agent_count: int
    actions: list[VisibleAction]


class PromptListView(Contract):
    items: list[PromptView]
    actions: list[VisibleAction]


class PromptSampleCreate(Contract):
    title: Name
    input: dict[str, JsonValue]
    expected_constraints: list[str] = Field(default_factory=list, max_length=50)


class PromptSampleView(Contract):
    sample_id: str
    title: str
    input: dict[str, JsonValue] | None
    expected_constraints: list[str] | None
    created_at: datetime
    masked: bool


class PromptTestRequest(Contract):
    revision: Revision
    model_route_version: str
    sample_id: str


class PromptDebugDescriptor(Contract):
    test_id: str
    scope: Scope
    purpose: Literal["debug"] = "debug"
    prompt: ResourceVersion
    model_route_version: str
    sample_id: str
    sample_digest: str
    rendered: PromptRenderView
    expected_constraints: list[str]
    descriptor_digest: str


class PromptDebugRun(Contract):
    run_id: str
    release_snapshot_id: str
    rendered_input_ref: str


class PromptDebugEvidence(Contract):
    scope: Scope
    run_id: str
    descriptor_digest: str
    model_route_version: str
    status: Literal["QUEUED", "RUNNING", "SUCCEEDED", "FAILED", "CANCELLED", "TIMED_OUT"]
    constraints_passed: bool
    usage_recorded: bool
    output: JsonValue = None


class PromptTestView(Contract):
    test_id: str
    version_id: str
    version_label: str
    draft_revision: int | None
    model_route_version: str
    model_route_name: str | None
    sample_title: str
    run_id: str | None
    status: DisplayStatus
    created_at: datetime
    snapshot: ResourceVersion
    rendered: PromptRenderView
    output: JsonValue
    masked: bool
    descriptor_digest: str


class PromptReference(Contract):
    source_resource_id: str
    source_version_id: str
    source_name: str | None
    version_label: str
    status: DisplayStatus
    target_version_id: str
    target_version_label: str
    resource_type_label: str


class PromptDifference(Contract):
    field: str
    label: str
    before: JsonValue
    after: JsonValue
    breaking: bool


class PromptCompareView(Contract):
    differences: list[PromptDifference]
    references: list[PromptReference]


class PromptImportRequest(Contract):
    resource: PromptCreate
    version_label: str = Field(min_length=1, max_length=64)
    format: Literal["text", "json"]
    data: str = Field(max_length=1000000)


class PromptExportRequest(Contract):
    format: Literal["text", "json"] = "json"


class PromptPortable(Contract):
    format_version: Literal[1] = 1
    content: PromptContent


class PromptRetireRequest(Contract):
    revision: Revision


class PromptRuntimeInput(Contract):
    input: dict[str, JsonValue] = Field(default_factory=dict)
    tool: dict[str, JsonValue] = Field(default_factory=dict)
    memory: dict[str, JsonValue] = Field(default_factory=dict)


class PromptDependencySummary(Contract):
    version_id: str
    content_digest: str
    dependencies_digest: str
    variables: list[PromptVariable]
    output_requirements: str


def json_dict(value: Contract) -> dict[str, Any]:
    return value.model_dump(mode="json")


class PromptRouteOption(Contract):
    version_id: str
    name: str | None
    version_label: str
