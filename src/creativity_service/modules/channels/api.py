"""渠道治理与服务认证路由，业务检查全部由服务层处理。"""

from typing import Annotated, cast

from fastapi import APIRouter, Depends, Query, Request, Response
from fastapi.security import HTTPAuthorizationCredentials

from creativity_service.core.auth.authentication import AdminSession
from creativity_service.core.auth.types import TokenResponse
from creativity_service.core.context import bearer_scheme
from creativity_service.core.primitives import ServiceError, new_id, unavailable
from creativity_service.modules.channels.assembly import ChannelServices
from creativity_service.modules.channels.presentation import (
    ChannelCreateOptions,
    ChannelPage,
    create_options,
    page_view,
)
from creativity_service.modules.channels.resources import ResourceCatalog, ResourceEntry
from creativity_service.modules.channels.schemas import (
    ChannelCreate,
    ChannelUpdate,
    ChannelView,
    ClientCreate,
    ClientUpdate,
    ClientView,
    DataScopeFromSource,
    DataScopeSourceInput,
    DataScopeUpdate,
    DataScopeView,
    EnvironmentCreate,
    EnvironmentUpdate,
    EnvironmentView,
    ImpactView,
    KeyCreate,
    KeyCreated,
    KeyRotate,
    KeyView,
    LifecycleAction,
    OverviewView,
    RevisionInput,
    TokenExchange,
    UsageQuery,
    UsageView,
)
from creativity_service.modules.iam.schemas import AuditFilter, AuditView, DirectoryPage
from creativity_service.modules.mcp.schemas import DataScopeDirectory, DataScopeSource
from creativity_service.modules.mcp.services import McpService

router = APIRouter(tags=["渠道管理"])
auth_router = APIRouter(tags=["服务认证"])


def services(request: Request) -> ChannelServices:
    value = getattr(request.app.state, "channels", None)
    if value is None:
        raise unavailable("渠道管理服务")
    return cast(ChannelServices, value)


Services = Annotated[ChannelServices, Depends(services)]


async def governance_session(
    request: Request,
    response: Response,
    service: Services,
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer_scheme)],
) -> AdminSession:
    response.headers["Cache-Control"] = "no-store"
    if credentials is None:
        raise ServiceError("UNAUTHENTICATED", "请重新登录", 401)
    return await service.channels.iam.authentication.admin_session(
        credentials.credentials,
        getattr(request.state, "request_id", None) or new_id("request"),
        governance=True,
    )


Session = Annotated[AdminSession, Depends(governance_session)]


@router.get("/channels", response_model=list[ChannelView])
async def channels(
    session: Session, service: Services, limit: Annotated[int, Query(ge=1, le=200)] = 100
) -> list[ChannelView]:
    return await service.channels.list_items(session, limit)


@router.get("/channel-create-options", response_model=ChannelCreateOptions)
async def channel_create_options(session: Session, service: Services) -> ChannelCreateOptions:
    return await create_options(service.channels, session)


@router.get("/channels/{channel_id}/page", response_model=ChannelPage)
async def channel_page(channel_id: str, session: Session, service: Services) -> ChannelPage:
    return await page_view(service.channels, session, channel_id)


@router.post("/channels", response_model=ChannelView, status_code=201)
async def create_channel(body: ChannelCreate, session: Session, service: Services) -> ChannelView:
    return await service.channels.create(session, body)


@router.get("/channels/page", response_model=DirectoryPage[ChannelView])
async def channels_page(
    session: Session,
    service: Services,
    limit: Annotated[int, Query(ge=1, le=200)] = 20,
    offset: Annotated[int, Query(ge=0)] = 0,
    search: Annotated[str, Query(max_length=128)] = "",
    status: Annotated[str | None, Query(pattern="^(ACTIVE|SUSPENDED|ARCHIVED)$")] = None,
) -> DirectoryPage[ChannelView]:
    return await service.channels.list_page(session, limit, offset, search, status)


@router.get("/channels/{channel_id}/resources", response_model=DirectoryPage[ResourceEntry])
async def channel_resources(
    channel_id: str,
    session: Session,
    service: Services,
    limit: Annotated[int, Query(ge=1, le=200)] = 20,
    offset: Annotated[int, Query(ge=0)] = 0,
    search: Annotated[str, Query(max_length=128)] = "",
    kind: str | None = None,
) -> DirectoryPage[ResourceEntry]:
    await service.channels.authorize(session, channel_id, "channel:manage")
    return await ResourceCatalog(
        service.channels.repository.engine, service.channels.iam.authorization
    ).page(session, channel_id, search=search, kind=kind, limit=limit, offset=offset)


@router.get("/channels/{channel_id}/audit-events/page", response_model=DirectoryPage[AuditView])
async def channel_audit_page(
    channel_id: str, session: Session, service: Services, query: Annotated[AuditFilter, Query()]
) -> DirectoryPage[AuditView]:
    return await service.channels.iam.audit.page(session, query, channel_id)


@router.get("/channels/{channel_id}", response_model=ChannelView)
async def detail(channel_id: str, session: Session, service: Services) -> ChannelView:
    return await service.channels.detail(session, channel_id)


@router.patch("/channels/{channel_id}", response_model=ChannelView)
async def update(
    channel_id: str, body: ChannelUpdate, session: Session, service: Services
) -> ChannelView:
    return await service.channels.update(session, channel_id, body)


@router.get("/channels/{channel_id}/overview", response_model=OverviewView)
async def overview(channel_id: str, session: Session, service: Services) -> OverviewView:
    return await service.channels.overview(session, channel_id)


@router.get("/channels/{channel_id}/audit-events", response_model=list[AuditView])
async def audit(
    channel_id: str,
    session: Session,
    service: Services,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
) -> list[AuditView]:
    return await service.channels.audit(session, channel_id, limit)


@router.get("/channels/{channel_id}/environments", response_model=list[EnvironmentView])
async def environments(
    channel_id: str, session: Session, service: Services
) -> list[EnvironmentView]:
    return await service.channels.environments(session, channel_id)


@router.post("/channels/{channel_id}/environments", response_model=EnvironmentView, status_code=201)
async def create_environment(
    channel_id: str, body: EnvironmentCreate, session: Session, service: Services
) -> EnvironmentView:
    return await service.channels.create_environment(session, channel_id, body)


@router.post(
    "/channels/{channel_id}/environments/{environment}/management-workspace", status_code=204
)
async def enable_management_workspace(
    channel_id: str, environment: str, session: Session, service: Services
) -> None:
    await service.channels.enable_management_workspace(session, channel_id, environment)


@router.patch("/channels/{channel_id}/environments/{environment}", response_model=EnvironmentView)
async def update_environment(
    channel_id: str, environment: str, body: EnvironmentUpdate, session: Session, service: Services
) -> EnvironmentView:
    return await service.channels.update_environment(session, channel_id, environment, body)


@router.get("/channels/{channel_id}/data-scopes", response_model=list[DataScopeView])
async def data_scopes(channel_id: str, session: Session, service: Services) -> list[DataScopeView]:
    return await service.channels.data_scopes(session, channel_id)


def directory_service(request: Request) -> McpService:
    value = getattr(request.app.state, "mcp", None)
    if value is None:
        raise unavailable("MCP 数据域目录")
    return cast(McpService, value)


DirectoryService = Annotated[McpService, Depends(directory_service)]


@router.get("/channels/{channel_id}/data-scope-sources", response_model=list[DataScopeSource])
async def data_scope_sources(
    channel_id: str, session: Session, service: Services, directory: DirectoryService
) -> list[DataScopeSource]:
    return await service.channels.data_scope_sources(session, channel_id, directory)


@router.post("/channels/{channel_id}/data-scope-directory", response_model=DataScopeDirectory)
async def data_scope_directory(
    channel_id: str,
    body: DataScopeSourceInput,
    session: Session,
    service: Services,
    directory: DirectoryService,
) -> DataScopeDirectory:
    return await service.channels.data_scope_directory(session, channel_id, body, directory)


@router.post(
    "/channels/{channel_id}/data-scopes/from-source", response_model=DataScopeView, status_code=201
)
async def create_data_scope_from_source(
    channel_id: str,
    body: DataScopeFromSource,
    session: Session,
    service: Services,
    directory: DirectoryService,
) -> DataScopeView:
    return await service.channels.create_data_scope_from_source(
        session, channel_id, body, directory
    )


@router.post("/channels/{channel_id}/data-scopes", status_code=410)
async def create_data_scope_legacy(channel_id: str, session: Session, service: Services) -> None:
    await service.channels.authorize(session, channel_id, "data_scope:manage")
    raise ServiceError("DATA_SCOPE_SOURCE_REQUIRED", "请从已配置的数据域目录选择范围", 410)


@router.patch("/channels/{channel_id}/data-scopes/{data_scope_id}", response_model=DataScopeView)
async def update_data_scope(
    channel_id: str, data_scope_id: str, body: DataScopeUpdate, session: Session, service: Services
) -> DataScopeView:
    return await service.channels.update_data_scope(session, channel_id, data_scope_id, body)


@router.get("/channels/{channel_id}/clients", response_model=list[ClientView])
async def clients(channel_id: str, session: Session, service: Services) -> list[ClientView]:
    return await service.channels.clients(session, channel_id)


@router.post("/channels/{channel_id}/clients", response_model=ClientView, status_code=201)
async def create_client(
    channel_id: str, body: ClientCreate, session: Session, service: Services
) -> ClientView:
    return await service.channels.create_client(session, channel_id, body)


@router.patch("/channels/{channel_id}/clients/{client_id}", response_model=ClientView)
async def update_client(
    channel_id: str, client_id: str, body: ClientUpdate, session: Session, service: Services
) -> ClientView:
    return await service.channels.update_client(session, channel_id, client_id, body)


@router.get("/channels/{channel_id}/keys", response_model=list[KeyView])
async def keys(channel_id: str, session: Session, service: Services) -> list[KeyView]:
    return await service.keys.list_items(session, channel_id)


@router.post("/channels/{channel_id}/keys", response_model=KeyCreated, status_code=201)
async def create_key(
    channel_id: str, body: KeyCreate, session: Session, service: Services
) -> KeyCreated:
    return await service.keys.create(session, channel_id, body)


@router.post(
    "/channels/{channel_id}/keys/{key_id}/rotate", response_model=KeyCreated, status_code=201
)
async def rotate_key(
    channel_id: str, key_id: str, body: KeyRotate, session: Session, service: Services
) -> KeyCreated:
    return await service.keys.rotate(session, channel_id, key_id, body)


@router.post("/channels/{channel_id}/keys/{key_id}/revoke", response_model=KeyView)
async def revoke_key(
    channel_id: str, key_id: str, body: RevisionInput, session: Session, service: Services
) -> KeyView:
    return await service.keys.revoke(session, channel_id, key_id, body.revision)


@router.get("/channels/{channel_id}/impact", response_model=ImpactView)
async def impact(
    channel_id: str, action: LifecycleAction, session: Session, service: Services
) -> ImpactView:
    return await service.lifecycle.preview(session, channel_id, action)


@router.post("/channels/{channel_id}/suspend", response_model=ChannelView)
async def suspend(
    channel_id: str, body: RevisionInput, session: Session, service: Services
) -> ChannelView:
    return await service.lifecycle.change(session, channel_id, "suspend", body.revision)


@router.post("/channels/{channel_id}/resume", response_model=ChannelView)
async def resume(
    channel_id: str, body: RevisionInput, session: Session, service: Services
) -> ChannelView:
    return await service.lifecycle.change(session, channel_id, "resume", body.revision)


@router.post("/channels/{channel_id}/archive", response_model=ChannelView)
async def archive(
    channel_id: str, body: RevisionInput, session: Session, service: Services
) -> ChannelView:
    return await service.lifecycle.change(session, channel_id, "archive", body.revision)


@router.get("/channels/{channel_id}/usage", response_model=UsageView)
async def usage(
    channel_id: str, query: Annotated[UsageQuery, Query()], session: Session, service: Services
) -> UsageView:
    return await service.channels.usage(session, channel_id, query)


@router.get("/platform/usage", response_model=list[UsageView])
async def platform_usage(
    channel_ids: Annotated[list[str], Query(min_length=1, max_length=200)],
    start_at: str,
    end_at: str,
    session: Session,
    service: Services,
) -> list[UsageView]:
    return await service.channels.platform_usage(session, channel_ids, start_at, end_at)


@router.get("/platform/usage/channels", response_model=DirectoryPage[dict[str, str]])
async def platform_usage_channels(
    session: Session,
    service: Services,
    search: Annotated[str, Query(max_length=128)] = "",
    offset: Annotated[int, Query(ge=0)] = 0,
    limit: Annotated[int, Query(ge=1, le=200)] = 20,
) -> DirectoryPage[dict[str, str]]:
    return await service.channels.usage_channels(session, search, offset, limit)


@auth_router.post("/auth/token", response_model=TokenResponse)
async def exchange_token(
    body: TokenExchange, request: Request, response: Response, service: Services
) -> TokenResponse:
    response.headers["Cache-Control"] = "no-store"
    return await service.keys.exchange(
        body, getattr(request.state, "request_id", None) or new_id("request")
    )
