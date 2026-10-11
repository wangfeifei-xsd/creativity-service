"""评测输入只描述样本与判定，不接收渠道、冻结快照或客户端评分。"""

import math
from typing import Any, Literal, Self

from pydantic import AwareDatetime, Field, model_validator
from pydantic_core import PydanticCustomError

from creativity_service.core.contracts import DisplayStatus, VisibleAction
from creativity_service.core.primitives import Contract, Digest, Identifier, Money, Revision
from creativity_service.core.versioning import validate_schema


class EvaluationSource(Contract):
    resource_type: Literal["run", "version", "artifact"]
    resource_id: Identifier


class Assertion(Contract):
    kind: Literal["schema", "equal", "numeric", "one_of", "subset", "forbidden", "citation"]
    path: str = Field(default="", max_length=512)
    expected: Any = None
    tolerance: float = Field(default=0, ge=0, allow_inf_nan=False)
    category: Literal[
        "quality",
        "schema",
        "cross_scope",
        "secret_leak",
        "fabricated_entity",
        "calculation",
        "authorization",
        "citation",
    ] = "quality"
    name: str = Field(min_length=1, max_length=128)

    @model_validator(mode="after")
    def valid(self) -> Self:
        if self.kind == "schema":
            if not isinstance(self.expected, dict):
                raise ValueError("结构断言需要 JSON Schema")
            validate_schema(self.expected)
        if self.kind in {"one_of", "subset", "forbidden"} and not isinstance(self.expected, list):
            raise ValueError("集合断言需要预期值列表")
        if self.kind == "numeric" and (
            type(self.expected) not in {int, float}
            or isinstance(self.expected, float)
            and not math.isfinite(self.expected)
        ):
            raise ValueError("精度断言需要数值预期")
        return self


class FixtureCall(Contract):
    tool_version_id: Identifier
    arguments: dict[str, Any]
    data: Any = None
    source_version: str = Field(min_length=1, max_length=128)
    observed_at: AwareDatetime
    error_code: Literal["TOOL_TIMEOUT", "TOOL_UNAVAILABLE", "TOOL_RESULT_INVALID"] | None = None


class HumanLabel(Contract):
    decision: Literal["approved", "rejected", "disputed"]
    reason: str = Field(min_length=1, max_length=2000)


class CaseInput(Contract):
    case_key: Identifier
    title: str = Field(min_length=1, max_length=255)
    input: dict[str, Any]
    context: dict[str, Any] = Field(default_factory=dict)
    assertions: list[Assertion] = Field(default_factory=list, max_length=100)
    expected_error: str | None = Field(default=None, max_length=128)
    labels: list[str] = Field(default_factory=list, max_length=32)
    label_source: str = Field(min_length=1, max_length=256)
    human_label: HumanLabel | None = None
    source_refs: list[EvaluationSource] = Field(default_factory=list, max_length=64)
    source_mode: Literal["all", "independent"] = "all"
    fixture: list[FixtureCall] = Field(default_factory=list, max_length=100)

    @model_validator(mode="after")
    def meaningful(self) -> Self:
        if set(self.input) & set(self.context):
            raise ValueError("输入与上下文字段不能重名")
        if not self.assertions and not self.expected_error and not self.human_label:
            raise PydanticCustomError(
                "evaluation_criteria_missing", "样本需要确定性断言、预期拒绝或人工判定标准"
            )
        return self


class DatasetCreate(Contract):
    name: str = Field(min_length=1, max_length=128)
    scenario: str = Field(min_length=1, max_length=64)
    owner: str = Field(min_length=1, max_length=128)
    applicability: str = Field(min_length=1, max_length=4000)


class DatasetVersionInput(Contract):
    revision: Revision
    version_label: str = Field(min_length=1, max_length=128)
    cases: list[CaseInput] = Field(min_length=1, max_length=1000)
    reference_versions: list[Identifier] = Field(default_factory=list, max_length=100)
    captured_at: AwareDatetime


class CaseEdit(Contract):
    revision: Revision
    case: CaseInput
    version_label: str = Field(min_length=1, max_length=128)


class ImportInput(Contract):
    format: Literal["jsonl", "csv"]
    content: str = Field(min_length=1, max_length=2_000_000)
    mapping: dict[str, str] = Field(default_factory=dict)
    revision: Revision
    version_label: str = Field(min_length=1, max_length=128)
    captured_at: AwareDatetime
    commit: bool = False
    preview_digest: Digest | None = None


class ImportRow(Contract):
    row_number: int
    case: CaseInput | None
    errors: list[str]


class ImportPreview(Contract):
    columns: list[str]
    mapping: dict[str, str]
    rows: list[ImportRow]
    valid_count: int
    error_count: int
    preview_digest: Digest
    version_id: str | None = None


class RunCaseInput(Contract):
    revision: Revision
    run_id: Identifier
    case_key: Identifier
    title: str = Field(min_length=1, max_length=255)
    input_paths: list[str] = Field(min_length=1, max_length=100)
    assertions: list[Assertion] = Field(min_length=1, max_length=100)
    label_source: str = Field(min_length=1, max_length=256)
    review: HumanLabel
    version_label: str = Field(min_length=1, max_length=128)


class CandidateInput(Contract):
    version_id: Identifier
    revision: Revision


class EvaluationBudget(Contract):
    max_runs: int = Field(ge=1, le=10000)
    token_limit: int = Field(ge=1, le=1_000_000_000)
    cost_limit: Money | None = None


class EvaluationCreate(Contract):
    experiment_prompt_id: Identifier | None = None
    name: str = Field(min_length=1, max_length=128)
    dataset_version_id: Identifier
    candidates: list[CandidateInput] = Field(min_length=1, max_length=8)
    baseline_candidate_version_id: Identifier | None = None
    baseline_evaluation_id: Identifier | None = None
    execution_mode: Literal["fixture", "live_readonly"] = "fixture"
    concurrency: int = Field(default=2, ge=1, le=16)
    budget: EvaluationBudget
    minimum_pass_rate: float = Field(default=1, ge=0, le=1)
    maximum_regression: float = Field(default=0, ge=0, le=1)
    release_target: bool = False
    expires_days: int = Field(default=7, ge=1, le=30)


class EvaluationControl(Contract):
    revision: Revision
    operation: Literal["pause", "resume", "cancel"]


class EvaluationReview(Contract):
    revision: Revision
    label: HumanLabel


class RerunInput(Contract):
    revision: Revision


class EvaluationCaseView(Contract):
    case_id: str
    case_key: str
    title: str
    valid: bool
    payload: CaseInput | None


class DatasetVersionView(Contract):
    version_id: str
    version_label: str
    content_digest: str
    captured_at: AwareDatetime
    reference_versions: list[str]
    cases: list[EvaluationCaseView]


class DatasetView(DatasetCreate):
    dataset_id: str
    revision: int
    current_version_id: str | None
    versions: list[DatasetVersionView] = Field(default_factory=list)
    actions: list[VisibleAction] = Field(default_factory=list)


class DatasetList(Contract):
    items: list[DatasetView]
    actions: list[VisibleAction]


class EvaluationView(Contract):
    evaluation_id: str
    name: str
    dataset_name: str
    dataset_version_label: str
    revision: int
    state: DisplayStatus
    execution_mode: str
    execution_mode_label: str
    external_data_may_change: bool
    candidates: list[dict[str, Any]]
    config: dict[str, Any]
    human_review: dict[str, Any] | None
    created_at: AwareDatetime
    actions: list[VisibleAction]


class EvaluationList(Contract):
    items: list[EvaluationView]
    actions: list[VisibleAction]


class ComparisonReport(Contract):
    report_id: str
    evaluation_id: str
    revision: int
    report_digest: str
    reproducible: bool
    complete: bool
    candidates: list[dict[str, Any]]
    results: list[dict[str, Any]]
    cost: dict[str, Any]
    latency: dict[str, Any]
    baseline: dict[str, Any] | None
    human_review: dict[str, Any] | None
    warnings: list[str]
