"""认证与身份管理传输层，业务校验统一委托服务。"""

from typing import Annotated, Any, cast

from fastapi import APIRouter, Depends, Query, Request, Response
from fastapi.security import HTTPAuthorizationCredentials

from creativity_service.core.auth.authentication import AdminSession
from creativity_service.core.auth.types import TokenResponse, WorkspaceOption
from creativity_service.core.context import bearer_scheme
from creativity_service.core.primitives import ServiceError, new_id, unavailable
from creativity_service.modules.iam.custom_roles import CustomRoles, RoleSave
from creativity_service.modules.iam.external import ExternalIdentity, IdentityExchange
from creativity_service.modules.iam.presentation import AccessOptions, access_options
from creativity_service.modules.iam.schemas import (
    AccountCreate,
    AccountUpdate,
    AccountView,
    AuditView,
    ChannelContextInput,
    GrantInput,
    GrantView,
    LoginInput,
    MembershipInput,
    MembershipView,
    PasswordChange,
    PasswordReset,
    RoleView,
    SessionView,
)
from creativity_service.modules.iam.services import IamServices

router = APIRouter(tags=["账号与权限"])


def services(request: Request) -> IamServices:
    value = getattr(request.app.state, "iam", None)
    if value is None:
        raise unavailable("账号认证服务")
    return cast(IamServices, value)


async def admin_session(
    request: Request,
    response: Response,
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer_scheme)],
    iam: Annotated[IamServices, Depends(services)],
) -> AdminSession:
    response.headers["Cache-Control"] = "no-store"
    if credentials is None:
        raise ServiceError("UNAUTHENTICATED", "请重新登录", 401)
    return await iam.authentication.admin_session(
        credentials.credentials,
        getattr(request.state, "request_id", None) or new_id("request"),
        allow_initial=request.url.path
        in {"/admin/v1/auth/logout", "/admin/v1/auth/change-password"},
        governance=request.url.path
        in {
            "/admin/v1/auth/session",
            "/admin/v1/auth/channels",
            "/admin/v1/auth/channel-context",
            "/admin/v1/auth/platform-context",
            "/admin/v1/auth/logout",
        },
    )


Session = Annotated[AdminSession, Depends(admin_session)]
Services = Annotated[IamServices, Depends(services)]


@router.post("/auth/login", response_model=TokenResponse)
async def login(
    body: LoginInput, request: Request, response: Response, iam: Services
) -> TokenResponse:
    response.headers["Cache-Control"] = "no-store"
    return await iam.sessions.login(
        body,
        request.client.host if request.client else "unknown",
        getattr(request.state, "request_id", None) or new_id("request"),
    )


@router.post("/auth/logout", status_code=204)
async def logout(session: Session, iam: Services) -> None:
    await iam.sessions.logout(session)


@router.post("/auth/change-password", status_code=204)
async def change_password(body: PasswordChange, session: Session, iam: Services) -> None:
    await iam.accounts.change_password(session, body)


@router.get("/auth/session", response_model=SessionView)
async def session_view(session: Session, iam: Services) -> SessionView:
    return await iam.sessions.view(session)


@router.get("/auth/channels", response_model=list[WorkspaceOption])
async def channels(session: Session, iam: Services) -> list[WorkspaceOption]:
    return await iam.sessions.channels(session)


@router.post("/auth/channel-context", response_model=TokenResponse)
async def channel_context(
    body: ChannelContextInput, session: Session, iam: Services
) -> TokenResponse:
    return await iam.sessions.enter(session, body)


@router.post("/auth/platform-context", response_model=TokenResponse)
async def platform_context(session: Session, iam: Services) -> TokenResponse:
    return await iam.sessions.enter_platform(session)


@router.get("/channels/{channel_id}/access-options", response_model=AccessOptions)
async def channel_access_options(channel_id: str, session: Session, iam: Services) -> AccessOptions:
    return await access_options(iam, session, channel_id)


@router.get("/accounts", response_model=list[AccountView])
async def accounts(
    session: Session, iam: Services, limit: Annotated[int, Query(ge=1, le=200)] = 100
) -> list[AccountView]:
    return await iam.accounts.list(session, limit)


@router.post("/accounts", response_model=AccountView, status_code=201)
async def create_account(body: AccountCreate, session: Session, iam: Services) -> AccountView:
    return await iam.accounts.create(session, body)


@router.patch("/accounts/{user_id}", response_model=AccountView)
async def update_account(
    user_id: str, body: AccountUpdate, session: Session, iam: Services
) -> AccountView:
    return await iam.accounts.update(session, user_id, body)


@router.post("/accounts/{user_id}/reset-password", status_code=204)
async def reset_password(
    user_id: str, body: PasswordReset, session: Session, iam: Services
) -> None:
    await iam.accounts.reset(session, user_id, body)


@router.get("/roles", response_model=list[RoleView])
async def roles(session: Session, iam: Services) -> list[RoleView]:
    return await iam.access.roles(session)


@router.get("/channels/{channel_id}/members", response_model=list[MembershipView])
async def members(channel_id: str, session: Session, iam: Services) -> list[MembershipView]:
    return await iam.access.list_members(session, channel_id)


@router.put("/channels/{channel_id}/members/{user_id}", response_model=MembershipView)
async def put_member(
    channel_id: str, user_id: str, body: MembershipInput, session: Session, iam: Services
) -> MembershipView:
    return await iam.access.put_member(session, channel_id, user_id, body)


@router.delete("/channels/{channel_id}/members/{user_id}", status_code=204)
async def remove_member(
    channel_id: str,
    user_id: str,
    revision: Annotated[int, Query(ge=1)],
    session: Session,
    iam: Services,
) -> None:
    await iam.access.remove_member(session, channel_id, user_id, revision)


@router.get("/channels/{channel_id}/resource-grants", response_model=list[GrantView])
async def grants(channel_id: str, session: Session, iam: Services) -> list[GrantView]:
    return await iam.access.list_grants(session, channel_id)


@router.put("/channels/{channel_id}/resource-grants/{grant_id}", response_model=GrantView)
async def put_grant(
    channel_id: str, grant_id: str, body: GrantInput, session: Session, iam: Services
) -> GrantView:
    return await iam.access.put_grant(session, channel_id, grant_id, body)


@router.delete("/channels/{channel_id}/resource-grants/{grant_id}", status_code=204)
async def revoke_grant(
    channel_id: str,
    grant_id: str,
    revision: Annotated[int, Query(ge=1)],
    session: Session,
    iam: Services,
) -> None:
    await iam.access.revoke_grant(session, channel_id, grant_id, revision)


@router.get("/audit-events", response_model=list[AuditView])
async def audit_events(
    session: Session, iam: Services, limit: Annotated[int, Query(ge=1, le=200)] = 50
) -> list[AuditView]:
    return await iam.audit.query(session, limit)


@router.get("/auth/identity-providers")
async def identity_providers(iam: Services) -> list[dict[str, str]]:
    return ExternalIdentity(iam).profiles()


@router.post("/auth/external-token", response_model=TokenResponse)
async def external_token(
    body: IdentityExchange, request: Request, response: Response, iam: Services
) -> TokenResponse:
    response.headers["Cache-Control"] = "no-store"
    return await ExternalIdentity(iam).exchange(
        body,
        request.client.host if request.client else "unknown",
        getattr(request.state, "request_id", None) or new_id("request"),
    )


@router.get("/custom-roles")
async def custom_roles(session: Session, iam: Services) -> list[dict[str, Any]]:
    return await CustomRoles(iam.access).list(session)


@router.post("/custom-roles", status_code=201)
async def create_role(session: Session, iam: Services, body: RoleSave) -> dict[str, Any]:
    return await CustomRoles(iam.access).save(session, body)


@router.patch("/custom-roles/{identifier}")
async def edit_role(
    session: Session, iam: Services, identifier: str, body: RoleSave
) -> dict[str, Any]:
    return await CustomRoles(iam.access).save(session, body, identifier)
