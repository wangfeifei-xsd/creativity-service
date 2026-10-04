"""仅登记管理 API；业务客户端不能直接执行任意工具。"""

from typing import Annotated

from fastapi import APIRouter, Depends, Query, Request

from creativity_service.core.context import AuthContext, require_http_context
from creativity_service.core.primitives import unavailable
from creativity_service.modules.tools.assembly import ToolServices
from creativity_service.modules.tools.schemas import (
    BindingOption,
    ExecutionOptions,
    ToolCallView,
    ToolCreate,
    ToolDetail,
    ToolEdit,
    ToolImpact,
    ToolList,
    ToolRelease,
    ToolRevision,
    ToolTestDescription,
    ToolTestInput,
    ToolTestResult,
    ToolVersionCreate,
    ToolVersionEdit,
    ToolVersionView,
    ToolView,
)
from creativity_service.modules.tools.services import ToolService

router = APIRouter(tags=["工具管理"])
Context = Annotated[AuthContext, Depends(require_http_context)]


def services(request: Request) -> ToolService:
    bundle: ToolServices | None = getattr(request.app.state, "tools", None)
    if bundle is None:
        raise unavailable("工具管理服务")
    return bundle.management


Services = Annotated[ToolService, Depends(services)]


@router.get("/tool-execution-options")
async def execution_options(
    context: Context, service: Services, tool_id: str = "new"
) -> ExecutionOptions:
    return await service.execution_options(context, tool_id)


@router.get("/tools", response_model=ToolList)
async def list_tools(
    context: Context,
    service: Services,
    source_type: str | None = None,
    status: str | None = None,
    effect_type: str | None = None,
    search: Annotated[str | None, Query(max_length=128)] = None,
    referenced_by: str | None = None,
) -> ToolList:
    return await service.list_tools(
        context, source_type, status, effect_type, search, referenced_by
    )


@router.get("/tool-bindings", response_model=list[BindingOption])
async def bindings(
    context: Context, service: Services, tool_id: str | None = None
) -> list[BindingOption]:
    return await service.bindings(context, tool_id)


@router.post("/tools", response_model=ToolView, status_code=201)
async def create(context: Context, service: Services, body: ToolCreate) -> ToolView:
    return await service.create(context, body)


@router.get("/tools/{tool_id}", response_model=ToolDetail)
async def detail(context: Context, service: Services, tool_id: str) -> ToolDetail:
    return await service.detail(context, tool_id)


@router.patch("/tools/{tool_id}", response_model=ToolView)
async def edit(context: Context, service: Services, tool_id: str, body: ToolEdit) -> ToolView:
    return await service.edit(context, tool_id, body)


@router.post("/tools/{tool_id}/versions", response_model=ToolVersionView, status_code=201)
async def create_version(
    context: Context, service: Services, tool_id: str, body: ToolVersionCreate
) -> ToolVersionView:
    return await service.create_version(context, tool_id, body)


@router.get("/tool-versions/{version_id}", response_model=ToolVersionView)
async def version(context: Context, service: Services, version_id: str) -> ToolVersionView:
    return await service.version_detail(context, version_id)


@router.patch("/tool-versions/{version_id}", response_model=ToolVersionView)
async def edit_version(
    context: Context, service: Services, version_id: str, body: ToolVersionEdit
) -> ToolVersionView:
    return await service.edit_version(context, version_id, body)


@router.post("/tool-versions/{version_id}/freeze", response_model=ToolVersionView)
async def freeze(
    context: Context, service: Services, version_id: str, body: ToolRevision
) -> ToolVersionView:
    return await service.freeze(context, version_id, body.revision)


@router.post("/tools/{tool_id}/releases", response_model=ToolDetail)
async def release(
    context: Context, service: Services, tool_id: str, body: ToolRelease
) -> ToolDetail:
    return await service.release(context, tool_id, body)


@router.get("/tools/{tool_id}/impact", response_model=ToolImpact)
async def impact(context: Context, service: Services, tool_id: str) -> ToolImpact:
    return await service.impact(context, tool_id)


@router.post("/tools/{tool_id}/disable", response_model=ToolDetail)
async def disable(
    context: Context, service: Services, tool_id: str, body: ToolRevision
) -> ToolDetail:
    return await service.disable(context, tool_id, body.revision)


@router.get("/tool-versions/{version_id}/test-description", response_model=ToolTestDescription)
async def test_description(
    context: Context, service: Services, version_id: str
) -> ToolTestDescription:
    return await service.test_description(context, version_id)


@router.post("/tool-versions/{version_id}/tests", response_model=ToolTestResult)
async def test(
    context: Context, service: Services, version_id: str, body: ToolTestInput
) -> ToolTestResult:
    return await service.test(context, version_id, body)


@router.get("/tool-calls", response_model=list[ToolCallView])
async def calls(
    context: Context, service: Services, tool_id: str | None = None
) -> list[ToolCallView]:
    return await service.calls(context, tool_id)


@router.get("/tool-calls/{call_id}", response_model=ToolCallView)
async def call(context: Context, service: Services, call_id: str) -> ToolCallView:
    return await service.call(context, call_id)
