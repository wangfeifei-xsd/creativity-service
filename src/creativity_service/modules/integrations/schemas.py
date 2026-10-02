"""连接与独立委托密钥的管理契约。"""

from pydantic import AwareDatetime, Field

from creativity_service.core.contracts import VisibleAction
from creativity_service.core.primitives import Contract, Identifier, Revision
from creativity_service.integrations.business.base import Capability, Operation


class IntegrationCreate(Contract):
    name: str = Field(min_length=1, max_length=128)
    adapter_code: Identifier
    adapter_version: str = Field(min_length=1, max_length=64)
    business_endpoint: str = Field(min_length=1, max_length=2048)
    credential_ref: Identifier
    allowed_operations: list[Operation] = Field(min_length=1, max_length=8)
    operation_paths: dict[Operation, str]
    field_mapping: dict[str, str] = Field(default_factory=dict)


class IntegrationEdit(IntegrationCreate):
    revision: Revision
    status: str = Field(pattern="^(ACTIVE|DISABLED)$")


class IntegrationView(Contract):
    integration_id: Identifier
    name: str
    environment_name: str
    data_scope_name: str
    adapter_name: str
    adapter_code: str
    adapter_version: str
    business_endpoint: str
    credential_ref: str
    allowed_operations: list[Operation]
    operation_paths: dict[Operation, str]
    field_mapping: dict[str, str]
    health: str
    health_name: str
    status: str
    status_name: str
    contract_version: str
    revision: Revision
    actions: list[VisibleAction]


class IntegrationList(Contract):
    items: list[IntegrationView]
    actions: list[VisibleAction]


class AdapterOption(Contract):
    code: str
    name: str
    version: str
    capabilities: list[Capability]


class NamedOption(Contract):
    value: str
    label: str


class IntegrationOptions(Contract):
    adapters: list[AdapterOption]
    clients: list[NamedOption]
    operations: list[NamedOption]


class BusinessCapabilityView(Capability):
    supported: bool
    allowed: bool
    verified: bool


class ContractCase(Contract):
    operation: Operation
    arguments: dict[str, str | int | bool | None] = Field(default_factory=dict)


class ContractTestInput(Contract):
    revision: Revision
    cases: list[ContractCase] = Field(min_length=1, max_length=8)


class ContractCaseResult(Contract):
    operation: Operation
    name: str
    passed: bool
    code: str | None = None
    message: str
    item_count: int | None = None


class ContractTestView(Contract):
    test_id: Identifier
    integration_id: Identifier
    config_revision: Revision
    state: str
    state_name: str
    results: list[ContractCaseResult]
    capabilities: list[Operation]
    created_at: AwareDatetime


class DelegationKeyCreate(Contract):
    client_id: Identifier
    issuer: str = Field(min_length=1, max_length=128)
    audience: str = Field(min_length=1, max_length=128)
    expires_at: AwareDatetime
    max_ttl_seconds: int = Field(default=300, ge=30, le=900)
    clock_skew_seconds: int = Field(default=30, ge=0, le=60)


class DelegationKeyView(Contract):
    kid: Identifier
    client_id: Identifier
    client_name: str
    issuer: str
    audience: str
    max_ttl_seconds: int
    clock_skew_seconds: int
    status: str
    status_name: str
    not_before: AwareDatetime
    expires_at: AwareDatetime
    rotated_from: str | None
    revision: Revision


class DelegationKeyIssued(Contract):
    key: DelegationKeyView
    signing_secret: str = Field(repr=False)


class DelegationKeyRotate(Contract):
    revision: Revision
    expires_at: AwareDatetime
    overlap_seconds: int = Field(default=330, ge=0, le=86400)


class RevisionInput(Contract):
    revision: Revision
