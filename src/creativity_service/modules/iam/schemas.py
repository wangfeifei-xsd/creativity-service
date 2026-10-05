"""管理接口输入与已过滤的界面响应。"""

from datetime import datetime
from typing import Literal

from pydantic import Field, SecretStr

from creativity_service.core.auth.types import Status, WorkspaceOption
from creativity_service.core.context import Environment
from creativity_service.core.contracts import NavigationItem, VisibleAction
from creativity_service.core.primitives import Contract, Identifier, Revision

ChannelRole = Identifier


class LoginInput(Contract):
    login_name: str = Field(min_length=1, max_length=128)
    password: SecretStr = Field(min_length=1, max_length=256)
    captcha_token: SecretStr = Field(min_length=43, max_length=43)


class CaptchaChallengeInput(Contract):
    login_name: str = Field(min_length=1, max_length=128)


class CaptchaChallenge(Contract):
    challenge_id: str
    background: str
    piece: str
    width: int
    height: int
    piece_size: int
    piece_y: int
    expires_in: int


class CaptchaVerifyInput(Contract):
    challenge_id: str = Field(pattern=r"^[A-Za-z0-9_-]{43}$")
    offset: float = Field(ge=0, le=268, allow_inf_nan=False)


class CaptchaVerification(Contract):
    captcha_token: str
    expires_in: int


class AccountCreate(Contract):
    login_name: str = Field(min_length=3, max_length=128)
    display_name: str = Field(min_length=1, max_length=128)
    initial_password: SecretStr = Field(min_length=12, max_length=256)
    platform_roles: list[Identifier] = Field(default_factory=list, max_length=50)
    role: Identifier | None = None
    channel_ids: list[Identifier] | None = Field(default=None, max_length=200)


class AccountUpdate(Contract):
    revision: Revision
    display_name: str | None = Field(default=None, min_length=1, max_length=128)
    status: Status | None = None
    platform_roles: list[Identifier] | None = Field(default=None, max_length=50)
    role: Identifier | None = None
    channel_ids: list[Identifier] | None = Field(default=None, max_length=200)


class PasswordReset(Contract):
    revision: Revision
    initial_password: SecretStr = Field(min_length=12, max_length=256)


class PasswordChange(Contract):
    current_password: SecretStr = Field(min_length=1, max_length=256)
    new_password: SecretStr = Field(min_length=12, max_length=256)


class AccountView(Contract):
    user_id: str
    login_name: str
    display_name: str
    platform_roles: list[str]
    platform_role_names: list[str]
    role: str | None
    role_name: str
    grant_scope: Literal["platform", "channel"] | None = None
    channel_ids: list[str] = Field(default_factory=list)
    channel_names: list[str | None] = Field(default_factory=list)
    status: Status
    status_label: str
    must_change_password: bool
    revision: int
    credential_updated_at: datetime
    updated_at: datetime | None = None


class DirectoryPage[T](Contract):
    items: list[T]
    total: int
    offset: int
    limit: int


class MembershipInput(Contract):
    revision: Revision | None = None
    roles: list[ChannelRole] = Field(min_length=1, max_length=5)
    environments: list[Environment] = Field(min_length=1, max_length=4)
    data_scopes: list[Identifier] = Field(min_length=1, max_length=200)
    status: Status = "ACTIVE"


class MembershipView(Contract):
    user_id: str
    display_name: str | None
    roles: list[str]
    role_names: list[str]
    environments: list[str]
    environment_names: list[str]
    data_scopes: list[str]
    data_scope_names: list[str | None]
    status: Status
    status_label: str
    revision: int


class GrantInput(Contract):
    revision: Revision | None = None
    grantee_type: Literal["account", "role"]
    grantee_id: Identifier
    resource_type: Identifier
    resource_id: str = Field(
        min_length=1, max_length=64, pattern=r"^(\*|[A-Za-z0-9][A-Za-z0-9_.-]*)$"
    )
    allowed_actions: list[str] = Field(min_length=1, max_length=100)
    environments: list[Environment] = Field(min_length=1, max_length=4)
    data_scopes: list[Identifier] = Field(min_length=1, max_length=200)


class GrantView(GrantInput):
    grant_id: str
    grantee_name: str | None
    resource_name: str | None
    action_names: list[str]
    environment_names: list[str]
    data_scope_names: list[str | None]


class RoleView(Contract):
    role_code: str
    name: str
    grant_scope: Literal["platform", "channel"]
    grant_scope_name: str
    actions: list[VisibleAction]


class ChannelContextInput(Contract):
    channel_id: Identifier
    environment: Environment
    data_scope_id: Identifier


class UserView(Contract):
    user_id: str
    display_name: str
    login_name: str


class SessionView(Contract):
    user: UserView
    navigation: list[NavigationItem]
    actions: list[VisibleAction]
    workspace: WorkspaceOption | None
    workspace_options: list[WorkspaceOption] = Field(default_factory=list)
    default_workspace: WorkspaceOption | None = None
    can_access_platform: bool = False
    expires_at: datetime


class AuditView(Contract):
    event_id: str
    actor_id: str
    actor_name: str | None
    action: str
    action_name: str
    target_type: str
    target_id: str
    target_name: str | None
    outcome: str
    outcome_label: str
    time: datetime
    request_id: str
    changed_fields: list[str]
    environment_name: str | None = None
    data_scope_name: str | None = None
    details: dict[str, str] = Field(default_factory=dict)


class AuditFilter(Contract):
    limit: int = Field(default=20, ge=1, le=200)
    offset: int = Field(default=0, ge=0)
    search: str = Field(default="", max_length=128)
    request_id: str = Field(default="", max_length=128)
    outcome: Literal["SUCCEEDED", "DENIED"] | None = None
    start_at: datetime | None = None
    end_at: datetime | None = None
