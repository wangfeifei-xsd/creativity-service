"""当前主体复核协议只传递身份与授权上限，不承载业务字段映射。"""

from typing import Literal

from pydantic import AwareDatetime, Field

from creativity_service.core.context import Scope
from creativity_service.core.primitives import Contract, Identifier, Revision


class SubjectReviewRequest(Contract):
    protocol: Literal["creativity.subject-review.v1"] = "creativity.subject-review.v1"
    scope: Scope
    client_id: Identifier
    key_id: Identifier
    request_id: Identifier
    actions: list[str]
    resources: dict[str, list[str]]


class SubjectReviewResponse(Contract):
    protocol: Literal["creativity.subject-review.v1"]
    scope: Scope
    active: bool = Field(strict=True)
    actions: frozenset[str]
    agent_actions: frozenset[str]
    resources: dict[str, frozenset[str]]
    observed_at: AwareDatetime
    expires_at: AwareDatetime


class SubjectReviewSave(Contract):
    client_id: Identifier
    connection_id: Identifier
    discovery_id: Identifier
    remote_tool_name: str = Field(min_length=1, max_length=256)
    timeout_seconds: int = Field(default=5, ge=1, le=30, strict=True)
    revision: Revision | None = None
    enabled: bool = True


class SubjectReviewView(SubjectReviewSave):
    binding_id: Identifier
    revision: Revision
    client_name: str
    connection_name: str
    tool_name: str
    connection_revision: Revision
    schema_hash: str
    status_name: str
    unavailable_reason: str | None
