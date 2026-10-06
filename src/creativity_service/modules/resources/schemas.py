"""可复用资源的两态管理、引用和实际使用契约。"""

from datetime import datetime
from typing import Literal

from pydantic import Field

from creativity_service.core.contracts import DisplayStatus
from creativity_service.core.primitives import Contract, Revision
from creativity_service.modules.iam.schemas import AccessAction

ResourceKind = Literal["prompt", "model_route", "tool", "skill"]


class ResourceSummary(Contract):
    resource_id: str
    name: str
    status: DisplayStatus
    reference_count: int
    usage_count: int
    revision: int
    configuration_revision: int | None
    updated_at: datetime
    actions: list[AccessAction]


class ResourceSummaries(Contract):
    resource_ids: list[str] = Field(max_length=200)


class ResourceMutation(Contract):
    revision: Revision
    configuration_revision: Revision | None = None
    confirm_used: bool = False


class ResourceReferenceView(Contract):
    resource_id: str
    resource_type: str
    resource_type_label: str
    name: str
    status: DisplayStatus
    environments: list[str]


class ResourceReferencePage(Contract):
    items: list[ResourceReferenceView]
    total: int


class ResourceUse(Contract):
    run_id: str
    resource_name: str
    agent_name: str | None
    caller_name: str | None
    environment: str
    purpose: str
    used_at: datetime
    deleted: bool


class ResourceUsePage(Contract):
    items: list[ResourceUse]
    total: int
