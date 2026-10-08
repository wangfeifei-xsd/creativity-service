"""独立委托密钥的管理契约。"""

from pydantic import AwareDatetime, Field

from creativity_service.core.primitives import Contract, Identifier, Revision


class NamedOption(Contract):
    value: str
    label: str


class DelegationKeyOptions(Contract):
    clients: list[NamedOption]


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
