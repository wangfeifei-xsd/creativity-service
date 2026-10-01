"""仅供部署探针使用的存活与就绪接口。"""

from typing import Literal

from fastapi import APIRouter, Request, Response
from pydantic import BaseModel

from creativity_service.core.infrastructure import Infrastructure, ReadinessResponse

router = APIRouter(prefix="/health", tags=["健康检查"])


class LivenessResponse(BaseModel):
    status: Literal["ok"] = "ok"


@router.get(
    "/live", summary="存活检查", operation_id="get_liveness", response_model=LivenessResponse
)
async def live() -> LivenessResponse:
    return LivenessResponse()


@router.get(
    "/ready",
    summary="就绪检查",
    operation_id="get_readiness",
    response_model=ReadinessResponse,
    responses={503: {"model": ReadinessResponse, "description": "基础设施尚未就绪"}},
)
async def ready(request: Request, response: Response) -> ReadinessResponse:
    infrastructure: Infrastructure = request.app.state.infrastructure
    result = await infrastructure.check_readiness()
    response.status_code = 200 if result.status == "ready" else 503
    return result
