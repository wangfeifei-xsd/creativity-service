"""智能体管理接口只接收配置与输入，冻结快照由服务端构建。"""

from typing import Annotated

from fastapi import APIRouter, Depends, Query, Request

from creativity_service.core.context import AuthContext, require_http_context
from creativity_service.core.primitives import unavailable
from creativity_service.modules.agents.assistance import AgentAssistance
from creativity_service.modules.agents.assistance_schemas import (
    AssistanceApply,
    AssistanceRequest,
    AssistanceSaved,
    AssistanceTurn,
)
from creativity_service.modules.agents.schemas import (
    AgentCreate,
    AgentDetail,
    AgentEdit,
    AgentList,
    AgentOptions,
    AgentReleaseInput,
    AgentStateInput,
    AgentTestInput,
    AgentTestView,
    AgentValidateInput,
    AgentValidation,
    AgentVersionCreate,
    AgentVersionEdit,
    AgentVersionView,
)
from creativity_service.modules.agents.services import AgentService
from creativity_service.modules.releases.services import ReleaseService
from creativity_service.modules.runs.schemas import AdmissionReceipt

router = APIRouter(tags=["智能体与版本发布"])
Context = Annotated[AuthContext, Depends(require_http_context)]


def service(request: Request) -> AgentService:
    value: AgentService | None = getattr(request.app.state, "agents", None)
    if value is None:
        raise unavailable("智能体管理服务")
    return value


Services = Annotated[AgentService, Depends(service)]


@router.get("/agents", response_model=AgentList)
async def list_agents(
    context: Context,
    service: Services,
    search: Annotated[str | None, Query(max_length=128)] = None,
    published_only: bool = False,
) -> AgentList:
    return await service.list_agents(context, search, published_only=published_only)


@router.get("/agents/options", response_model=AgentOptions)
async def options(context: Context, service: Services) -> AgentOptions:
    return await service.options(context)


@router.post("/agents", response_model=AgentDetail, status_code=201)
async def create(context: Context, service: Services, body: AgentCreate) -> AgentDetail:
    return await service.create(context, body)


def assistance(request: Request) -> AgentAssistance:
    runtime = getattr(request.app.state, "runtime", None)
    value: AgentAssistance | None = getattr(runtime, "assistance", None)
    if value is None:
        raise unavailable("智能协助")
    return value


@router.post("/agents/assistance", response_model=AdmissionReceipt, status_code=202)
async def assist(context: Context, request: Request, body: AssistanceRequest) -> AdmissionReceipt:
    return await assistance(request).submit(context, body)


@router.get("/agents/assistance/runs/{run_id}", response_model=AssistanceTurn)
async def assistance_turn(context: Context, request: Request, run_id: str) -> AssistanceTurn:
    return await assistance(request).turn(context, run_id)


@router.post("/agents/assistance/runs/{run_id}/apply", response_model=AssistanceSaved)
async def assistance_apply(
    context: Context, request: Request, run_id: str, body: AssistanceApply | None = None
) -> AssistanceSaved:
    return await assistance(request).apply(context, run_id)


@router.get("/agents/{agent_id}", response_model=AgentDetail)
async def detail(context: Context, service: Services, agent_id: str) -> AgentDetail:
    return await service.detail(context, agent_id)


@router.patch("/agents/{agent_id}", response_model=AgentDetail)
async def edit(context: Context, service: Services, agent_id: str, body: AgentEdit) -> AgentDetail:
    return await service.edit(context, agent_id, body)


@router.post("/agents/{agent_id}/versions", response_model=AgentVersionView, status_code=201)
async def create_version(
    context: Context, service: Services, agent_id: str, body: AgentVersionCreate
) -> AgentVersionView:
    return await service.create_version(context, agent_id, body)


@router.get("/agent-versions/{version_id}", response_model=AgentVersionView)
async def version(context: Context, service: Services, version_id: str) -> AgentVersionView:
    return await service.version(context, version_id)


@router.patch("/agent-versions/{version_id}", response_model=AgentVersionView)
async def edit_version(
    context: Context, service: Services, version_id: str, body: AgentVersionEdit
) -> AgentVersionView:
    return await service.edit_version(context, version_id, body)


@router.post("/agent-versions/{version_id}/validate", response_model=AgentValidation)
async def validate(
    context: Context, service: Services, version_id: str, body: AgentValidateInput
) -> AgentValidation:
    return await service.validate(context, version_id, body)


@router.post("/agent-versions/{version_id}/tests", response_model=AgentTestView)
async def test(
    context: Context, service: Services, version_id: str, body: AgentTestInput
) -> AgentTestView:
    return await service.test(context, version_id, body)


@router.post("/agents/{agent_id}/releases", response_model=AgentDetail)
async def release(
    context: Context, service: Services, agent_id: str, body: AgentReleaseInput
) -> AgentDetail:
    return await ReleaseService(service).release(context, agent_id, body)


@router.post("/agents/{agent_id}/state", response_model=AgentDetail)
async def state(
    context: Context, service: Services, agent_id: str, body: AgentStateInput
) -> AgentDetail:
    return await ReleaseService(service).state(context, agent_id, body)
