"""统一删除入口与既有会话、记忆删除任务的进度查询。"""

from typing import Annotated

from fastapi import APIRouter, Depends, Request

from creativity_service.core.context import AuthContext, require_http_context
from creativity_service.core.primitives import unavailable
from creativity_service.modules.data_lifecycle.schemas import (
    LifecycleImpact,
    LifecycleProgress,
    LifecycleTarget,
)
from creativity_service.modules.data_lifecycle.services import DataLifecycleService

router = APIRouter(tags=["数据生命周期"])
Context = Annotated[AuthContext, Depends(require_http_context)]


def service(request: Request) -> DataLifecycleService:
    value = getattr(request.app.state, "data_lifecycle", None)
    if not isinstance(value, DataLifecycleService):
        raise unavailable("数据清理服务")
    return value


Lifecycle = Annotated[DataLifecycleService, Depends(service)]


@router.post("/data-lifecycle/deletion-preview")
async def preview(body: LifecycleTarget, context: Context, lifecycle: Lifecycle) -> LifecycleImpact:
    return LifecycleImpact.model_validate(
        await lifecycle.preview(context, body.resource_type, body.resource_id)
    )


@router.post("/data-lifecycle/deletions", status_code=202)
async def request_deletion(
    body: LifecycleTarget, context: Context, lifecycle: Lifecycle
) -> LifecycleProgress:
    return LifecycleProgress.model_validate(
        await lifecycle.request(context, body.resource_type, body.resource_id)
    )


@router.get("/deletions/{deletion_id}/progress")
async def progress(deletion_id: str, context: Context, lifecycle: Lifecycle) -> LifecycleProgress:
    return LifecycleProgress.model_validate(await lifecycle.progress(context, deletion_id))


@router.post("/deletions/{deletion_id}/retry")
async def retry(deletion_id: str, context: Context, lifecycle: Lifecycle) -> LifecycleProgress:
    return LifecycleProgress.model_validate(await lifecycle.retry(context, deletion_id))
