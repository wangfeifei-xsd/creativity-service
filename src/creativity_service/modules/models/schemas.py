"""模型配置、冻结依赖与调试交接契约。"""

from datetime import datetime
from typing import Any, Literal

from pydantic import Field, SecretStr

from creativity_service.core.context import Scope
from creativity_service.core.contracts import ResourceVersion, VisibleAction
from creativity_service.core.primitives import Contract, Identifier, Revision
from creativity_service.modules.iam.schemas import AccessAction

ProtocolType = Literal[
    "chat_completions", "responses", "anthropic_messages", "gemini_generate_content"
]
Capability = Literal["text", "tools", "structured_output", "streaming", "vision", "embedding"]
CapabilityState = Literal["SUPPORTED", "UNSUPPORTED", "UNVERIFIED"]
Status = Literal["ACTIVE", "DISABLED"]
TestCase = Literal["text", "schema", "tools", "stream_cancel", "usage", "embedding"]


class ProtocolView(Contract):
    code: ProtocolType
    name: str
    enabled: bool
    reason: str | None
    parameters: list[str]


class ProviderInput(Contract):
    id: Identifier | None = None
    code: str | None = Field(default=None, min_length=1, max_length=64)
    name: str = Field(min_length=1, max_length=128)
    protocols: list[ProtocolType] = Field(min_length=1, max_length=4)
    template_content: dict[str, Any] = Field(default_factory=dict)
    revision: Revision | None = None


class ProviderView(ProviderInput):
    id: str
    code: str


class ConnectionInput(Contract):
    name: str = Field(min_length=1, max_length=128)
    provider_id: Identifier
    protocol: ProtocolType
    endpoint: str = Field(min_length=1, max_length=2048)
    allowed_networks: list[str] = Field(default_factory=list, max_length=32)
    credential_ref: Identifier
    timeout_seconds: int = Field(default=60, ge=1, le=600)
    status: Status = "ACTIVE"
    revision: Revision | None = None


class ConnectionView(ConnectionInput):
    id: str
    provider_name: str | None
    protocol_name: str
    status_label: str
    health_status: str
    health_label: str
    health_reason: str | None
    health_checked_at: datetime | None
    current_version_id: str
    actions: list[VisibleAction] = Field(default_factory=list)


class CredentialInput(Contract):
    secret: SecretStr = Field(min_length=1, max_length=16384)


class CredentialView(Contract):
    credential_ref: str


class ModelInput(Contract):
    model_code: str = Field(min_length=1, max_length=64, pattern=r"^[a-z][a-z0-9_.-]*$")
    name: str = Field(min_length=1, max_length=128)
    connection_id: Identifier
    provider_model_name: str = Field(min_length=1, max_length=256)
    context_limit: int | None = Field(default=None, ge=1)
    status: Status = "ACTIVE"
    parameters: dict[str, Any] = Field(default_factory=dict)
    parameter_allowlist: list[str] = Field(default_factory=lambda: ["max_tokens"])
    revision: Revision | None = None


class CapabilityView(Contract):
    capability: Capability
    name: str
    state: CapabilityState
    label: str
    verified_at: datetime | None
    reason: str | None


class ModelView(ModelInput):
    protocol: ProtocolType
    usage_subsets: dict[str, str]
    id: str
    connection_name: str
    provider_name: str | None
    protocol_name: str
    status_label: str
    current_version_id: str
    config_digest: str
    capabilities: list[CapabilityView]
    verified_at: datetime | None
    parameter_reasons: dict[str, str]
    actions: list[AccessAction] = Field(default_factory=list)


class ConnectionTestView(Contract):
    model_id: str
    connection_id: str
    success: bool
    message: str
    error_code: str | None = None
    latency_ms: int = Field(ge=0)
    checked_at: datetime


class FrozenModel(Contract):
    scope: Scope
    model_id: str
    model_version_id: str
    model_revision: int
    model_name: str
    connection_id: str
    connection_version_id: str
    connection_revision: int
    provider_credential_id: str
    protocol: ProtocolType
    endpoint: str
    allowed_networks: list[str] = Field(default_factory=list, max_length=32)
    provider_model_name: str
    timeout_seconds: int
    parameters: dict[str, Any]
    parameter_allowlist: list[str]
    config_digest: str


class RetryPolicy(Contract):
    max_attempts: int = Field(default=3, ge=1, le=10)
    retries_per_model: int = Field(default=0, ge=0, le=2)


class RouteInput(Contract):
    code: str = Field(min_length=1, max_length=64, pattern=r"^[a-z][a-z0-9_.-]*$")
    name: str = Field(min_length=1, max_length=128)


class RouteVersionInput(Contract):
    name: str | None = Field(default=None, min_length=1, max_length=128)
    resource_revision: int | None = Field(default=None, ge=1)
    label: str = Field(default="当前配置", min_length=1, max_length=64)
    revision: int | None = Field(default=None, ge=1)
    primary_model: Identifier
    fallback_models: list[Identifier] = Field(default_factory=list, max_length=9)
    required_capabilities: list[Capability] = Field(default=["text"])
    parameters: dict[str, Any] = Field(default_factory=dict)
    retry_policy: RetryPolicy = Field(default_factory=RetryPolicy)
    hard_amount_budget: bool = False


class RouteView(RouteInput):
    id: str
    revision: int
    status: Status
    status_label: str
    released_version_id: str | None
    actions: list[VisibleAction] = Field(default_factory=list)


class ReleaseInput(Contract):
    version_id: Identifier
    expected_version_id: Identifier | None = None


class RouteVersionView(ResourceVersion):
    actions: list[AccessAction] = Field(default_factory=list)


class TestInput(Contract):
    cases: list[TestCase] = Field(default=["text", "usage"], min_length=1, max_length=6)


class CaseDefinition(Contract):
    case: TestCase
    name: str
    prompt: str
    capability: Capability | None = None
    output_schema: dict[str, Any] | None = None
    tools: list[dict[str, Any]] = Field(default_factory=list)
    cancel_after_chunks: int | None = None


class DebugExecution(Contract):
    test_id: str
    purpose: Literal["debug"] = "debug"
    configuration: FrozenModel
    cases: list[CaseDefinition]


class CaseResult(Contract):
    case: TestCase
    passed: bool
    reason: str | None = None
    attempt_ids: list[Identifier] = Field(min_length=1)


class TestCompletion(Contract):
    run_id: Identifier
    config_digest: str
    results: list[CaseResult] = Field(min_length=1)
    latency_ms: int = Field(ge=0)
    evidence: Literal["fixture", "live"]


class TestView(Contract):
    id: str
    model_id: str
    model_name: str
    config_revision: int
    config_digest: str
    cases: list[TestCase]
    results: list[CaseResult]
    state: str
    state_label: str
    error_code: str | None
    reason: str | None
    run_id: str | None
    attempt_ids: list[str]
    latency_ms: int | None
    created_at: datetime


class ModelGrantInput(Contract):
    grantee_type: Literal["account", "role"]
    grantee_id: Identifier
    revision: Revision | None = None


class PriceView(Contract):
    model_id: str
    available: bool
    source: str | None
    currency: str | None
    price_items: list[dict[str, Any]]
    reason: str | None


class ModelList(Contract):
    items: list[ModelView]
    actions: list[VisibleAction]


class ConnectionList(Contract):
    items: list[ConnectionView]
    actions: list[VisibleAction]


class RouteList(Contract):
    items: list[RouteView]
    actions: list[VisibleAction]
