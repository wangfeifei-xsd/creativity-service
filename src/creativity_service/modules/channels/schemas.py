"""渠道输入与页面交接契约，密钥明文只存在于一次性响应。"""

from datetime import datetime
from typing import Literal

from pydantic import AwareDatetime, Field, SecretStr

from creativity_service.core.auth.types import Status
from creativity_service.core.context import Environment
from creativity_service.core.contracts import VisibleAction
from creativity_service.core.primitives import Contract, Identifier, Money, Revision

ChannelStatus = Literal["ACTIVE", "SUSPENDED", "ARCHIVED"]
LifecycleAction = Literal["suspend", "resume", "archive"]


class RetentionPolicy(Contract):
    retention_days: int = Field(default=90, ge=1, le=3650)
    run_content_days: int = Field(default=30, ge=1, le=3650)
    metadata_days: int = Field(default=365, ge=1, le=3650)
    sse_hours: int = Field(default=24, ge=1, le=168)
    temporary_hours: int = Field(default=1, ge=1, le=24)
    export_days: int = Field(default=7, ge=1, le=90)


class ReleasePolicy(Contract):
    approval_required: bool = True


class InitialDataScope(Contract):
    name: str = Field(min_length=1, max_length=128)
    external_scope_type: str = Field(min_length=1, max_length=64)
    external_scope_id: str = Field(min_length=1, max_length=128)


class ChannelCreate(Contract):
    channel_code: str = Field(min_length=1, max_length=64)
    name: str = Field(min_length=1, max_length=128)
    owner: str = Field(min_length=1, max_length=128)
    business_type: str | None = Field(
        default=None, min_length=1, max_length=32, description="可选业务分类，仅用于展示"
    )
    first_admin_user_id: Identifier
    environment: Environment
    data_scope: InitialDataScope
    retention_policy: RetentionPolicy = Field(default_factory=RetentionPolicy)
    independent_actions: list[Literal["release:publish", "data:export", "data:read_sensitive"]] = (
        Field(default_factory=list, max_length=3)
    )


class ChannelUpdate(Contract):
    revision: Revision
    name: str | None = Field(default=None, min_length=1, max_length=128)
    owner: str | None = Field(default=None, min_length=1, max_length=128)
    retention_policy: RetentionPolicy | None = None


class EnvironmentCreate(Contract):
    environment: Environment
    name: str = Field(min_length=1, max_length=128)
    release_policy: ReleasePolicy = Field(default_factory=ReleasePolicy)
    retention_policy: RetentionPolicy = Field(default_factory=RetentionPolicy)


class EnvironmentUpdate(Contract):
    revision: Revision
    name: str | None = Field(default=None, min_length=1, max_length=128)
    status: Status | None = None
    release_policy: ReleasePolicy | None = None
    retention_policy: RetentionPolicy | None = None


class DataScopeCreate(InitialDataScope):
    environment: Environment
    administrator_id: Identifier | None = None


class DataScopeUpdate(Contract):
    revision: Revision
    name: str | None = Field(default=None, min_length=1, max_length=128)
    status: Status | None = None


class ClientCreate(Contract):
    name: str = Field(min_length=1, max_length=128)
    environment: Environment
    scopes: list[str] = Field(min_length=1, max_length=100)
    data_scopes: list[Identifier] = Field(min_length=1, max_length=200)


class ClientUpdate(Contract):
    revision: Revision
    name: str | None = Field(default=None, min_length=1, max_length=128)
    status: Status | None = None
    scopes: list[str] | None = Field(default=None, min_length=1, max_length=100)
    data_scopes: list[Identifier] | None = Field(default=None, min_length=1, max_length=200)


class KeyCreate(Contract):
    name: str = Field(min_length=1, max_length=128)
    client_id: Identifier
    environment: Environment
    scopes: list[str] = Field(min_length=1, max_length=100)
    expires_at: AwareDatetime


class KeyRotate(Contract):
    revision: Revision
    expires_at: AwareDatetime
    overlap_seconds: int = Field(ge=0, le=604800, strict=True)


class RevisionInput(Contract):
    revision: Revision


class TokenExchange(Contract):
    api_key: SecretStr = Field(min_length=1, max_length=512)


class ChannelView(Contract):
    channel_id: str
    channel_code: str
    name: str
    owner: str
    business_type: str | None
    business_type_name: str | None
    status: ChannelStatus
    status_label: str
    created_at: datetime
    archived_at: datetime | None
    retention_policy: RetentionPolicy
    revision: int
    actions: list[VisibleAction]


class EnvironmentView(Contract):
    environment: Environment
    name: str
    status: Status
    status_label: str
    release_policy: ReleasePolicy
    retention_policy: RetentionPolicy
    revision: int


class DataScopeView(Contract):
    data_scope_id: str
    environment: Environment
    environment_name: str | None
    name: str
    external_scope_type: str
    external_scope_type_name: str | None
    external_scope_id: str
    status: Status
    status_label: str
    revision: int


class ClientView(Contract):
    client_id: str
    name: str
    environment: Environment
    environment_name: str | None
    scopes: list[str]
    scope_names: list[str]
    data_scopes: list[str]
    data_scope_names: list[str | None]
    status: Status
    status_label: str
    revision: int


class KeyView(Contract):
    key_id: str
    name: str
    client_id: str
    client_name: str | None
    environment: Environment
    environment_name: str | None
    masked_key: str
    scopes: list[str]
    scope_names: list[str]
    status: Literal["ACTIVE", "REVOKED", "EXPIRED"]
    status_label: str
    expires_at: datetime
    last_used_at: datetime | None
    created_at: datetime
    revision: int


class KeyCreated(Contract):
    key: KeyView
    api_key: str = Field(repr=False)
    overlap_until: datetime | None = None


class ImpactView(Contract):
    channel_id: str
    channel_name: str
    action: LifecycleAction
    revision: int
    active_keys: int
    active_clients: int
    unfinished_tasks: int | None
    blockers: list[str]
    can_execute: bool


class LifecycleEvent(Contract):
    event_id: str
    channel_id: str
    event_type: str
    target_type: str
    target_id: str
    environment: Environment | None
    previous_status: str | None
    current_status: str
    revision: int
    occurred_at: datetime


class ResourceReference(Contract):
    resource_type: str
    resource_id: str
    name: str | None
    count: int | None = None


class OverviewView(Contract):
    channel: ChannelView
    environments: int
    data_scopes: int
    clients: int
    active_keys: int
    members_path: str
    audit_path: str
    resource_references: list[ResourceReference] | None


class UsageQuery(Contract):
    start_at: AwareDatetime
    end_at: AwareDatetime


class UsageView(Contract):
    channel_id: str
    channel_name: str
    start_at: datetime
    end_at: datetime
    calls: int = Field(ge=0)
    input_tokens: int | None = Field(ge=0)
    output_tokens: int | None = Field(ge=0)
    costs: list[Money]
    provisional_costs: list[Money] = Field(default_factory=list)
    requests: int | None = None
    missing_usage: int = 0
    unpriced: int = 0
    price_complete: bool = False
    aggregate_updated_at: datetime | None = None
