"""资源生命周期接口，配置内容仍由各模块的原子校验管理。"""

from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Query, Request, Response

from creativity_service.core.context import AuthContext, require_http_context
from creativity_service.modules.resources.schemas import (
    ResourceKind,
    ResourceMutation,
    ResourceReferencePage,
    ResourceSummaries,
    ResourceSummary,
    ResourceUsePage,
)
from creativity_service.modules.resources.services import ResourceManagement

router = APIRouter(prefix="/resource-management", tags=["资源管理"])
Context = Annotated[AuthContext, Depends(require_http_context)]


def service(request: Request) -> ResourceManagement:
    state = request.app.state
    tools = state.tools.management
    validators = {
        "prompt": state.prompts.versions.validator,
        "tool": tools.versions.validator,
        "skill": state.skills.versions.validator,
        "model_route": state.models.routing,
    }
    return ResourceManagement(tools.engine, state.iam.authorization, validators)


Service = Annotated[ResourceManagement, Depends(service)]


@router.post("/{kind}/summaries")
async def summaries(
    context: Context, service: Service, kind: ResourceKind, body: ResourceSummaries
) -> list[ResourceSummary]:
    return await service.summaries(context, kind, body.resource_ids)


@router.get("/{kind}/{identifier}/references")
async def references(
    context: Context,
    service: Service,
    kind: ResourceKind,
    identifier: str,
    offset: Annotated[int, Query(ge=0)] = 0,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
) -> ResourceReferencePage:
    return await service.references(context, kind, identifier, offset, limit)


@router.get("/{kind}/{identifier}/uses")
async def uses(
    context: Context,
    service: Service,
    kind: ResourceKind,
    identifier: str,
    offset: Annotated[int, Query(ge=0)] = 0,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
) -> ResourceUsePage:
    return await service.uses(context, kind, identifier, offset, limit)


@router.post("/{kind}/{identifier}/{operation}", status_code=204)
async def mutate(
    context: Context,
    service: Service,
    kind: ResourceKind,
    identifier: str,
    operation: Literal["publish", "unpublish", "delete"],
    body: ResourceMutation,
) -> Response:
    await service.mutate(context, kind, identifier, operation, body)
    return Response(status_code=204)
