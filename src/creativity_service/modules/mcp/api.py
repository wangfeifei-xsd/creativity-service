"""管理连接与发现接口；不暴露直接业务调用入口。"""

from typing import Annotated

from fastapi import APIRouter, Depends, Request

from creativity_service.core.context import AuthContext, require_http_context
from creativity_service.core.primitives import unavailable
from creativity_service.modules.mcp.schemas import (
    McpCheck,
    McpConnection,
    McpCreate,
    McpCredential,
    McpDetail,
    McpDiff,
    McpDiscovery,
    McpEdit,
    McpImpact,
    McpImport,
    McpImportInput,
    McpList,
    McpRevision,
)
from creativity_service.modules.mcp.services import McpService

router = APIRouter(prefix="/mcp-connections", tags=["MCP 连接"])
Context = Annotated[AuthContext, Depends(require_http_context)]


def service(request: Request) -> McpService:
    value: McpService | None = getattr(request.app.state, "mcp", None)
    if value is None:
        raise unavailable("MCP 连接服务")
    return value


Service = Annotated[McpService, Depends(service)]


@router.get("", response_model=McpList)
async def list_connections(context: Context, service: Service) -> McpList:
    return await service.list_connections(context)


@router.post("", response_model=McpConnection, status_code=201)
async def create(context: Context, service: Service, body: McpCreate) -> McpConnection:
    return await service.create(context, body)


@router.get("/{connection_id}", response_model=McpDetail)
async def detail(context: Context, service: Service, connection_id: str) -> McpDetail:
    return await service.detail(context, connection_id)


@router.patch("/{connection_id}", response_model=McpConnection)
async def edit(
    context: Context, service: Service, connection_id: str, body: McpEdit
) -> McpConnection:
    return await service.edit(context, connection_id, body)


@router.post("/{connection_id}/credentials", response_model=McpConnection)
async def credential(
    context: Context, service: Service, connection_id: str, body: McpCredential
) -> McpConnection:
    return await service.rotate(context, connection_id, body)


@router.post("/{connection_id}/test", response_model=McpCheck | McpDiscovery)
async def test(context: Context, service: Service, connection_id: str) -> McpCheck | McpDiscovery:
    return await service.probe(context, connection_id, False)


@router.post("/{connection_id}/discover", response_model=McpCheck | McpDiscovery)
async def discover(
    context: Context, service: Service, connection_id: str
) -> McpCheck | McpDiscovery:
    return await service.probe(context, connection_id, True)


@router.get("/{connection_id}/discoveries/{discovery_id}/diff", response_model=McpDiff)
async def diff(
    context: Context, service: Service, connection_id: str, discovery_id: str
) -> McpDiff:
    return await service.diff(context, connection_id, discovery_id)


@router.post("/{connection_id}/imports", response_model=McpImport, status_code=201)
async def import_tool(
    context: Context, service: Service, connection_id: str, body: McpImportInput
) -> McpImport:
    return await service.import_tool(context, connection_id, body)


@router.get("/{connection_id}/impact", response_model=McpImpact)
async def impact(context: Context, service: Service, connection_id: str) -> McpImpact:
    return await service.impact(context, connection_id)


@router.post("/{connection_id}/enable", response_model=McpConnection)
async def enable(
    context: Context, service: Service, connection_id: str, body: McpRevision
) -> McpConnection:
    return await service.set_enabled(context, connection_id, body.revision, True)


@router.post("/{connection_id}/disable", response_model=McpConnection)
async def disable(
    context: Context, service: Service, connection_id: str, body: McpRevision
) -> McpConnection:
    return await service.set_enabled(context, connection_id, body.revision, False)
