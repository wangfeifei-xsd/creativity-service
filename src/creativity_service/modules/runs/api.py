"""方案 17 注入正式解析器后登记此路由；正文没有可上传执行定义的入口。"""

from datetime import datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Header, Query, Request

from creativity_service.core.context import AuthContext, require_http_context
from creativity_service.core.contracts import ResultEnvelope
from creativity_service.modules.runs.assembly import require_runs
from creativity_service.modules.runs.schemas import (
    AdmissionReceipt,
    RerunInput,
    RunRequest,
    TracePage,
)
from creativity_service.modules.runs.services import RunService

router = APIRouter(prefix="/runs", tags=["任务运行"])
admin_router = APIRouter(prefix="/runs", tags=["运行管理"])
Context = Annotated[AuthContext, Depends(require_http_context)]
Key = Annotated[str, Header(alias="Idempotency-Key", min_length=1, max_length=128)]


def service(request: Request) -> RunService:
    return require_runs(getattr(request.app.state, "runs", None))


Runs = Annotated[RunService, Depends(service)]


@router.post("", status_code=202)
async def admit(body: RunRequest, key: Key, context: Context, runs: Runs) -> AdmissionReceipt:
    return await runs.admit_run(context, body, key)


@router.get("/{run_id}")
async def get_run(run_id: str, context: Context, runs: Runs) -> ResultEnvelope:
    return await runs.get_run(context, run_id)


@router.post("/{run_id}/cancel")
async def cancel(run_id: str, context: Context, runs: Runs) -> AdmissionReceipt:
    return await runs.cancel(context, run_id)


@admin_router.get("")
async def list_runs(
    context: Context,
    runs: Runs,
    cursor: str | None = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    state: str | None = None,
    agent_id: str | None = None,
    key_id: str | None = None,
    purpose: str | None = None,
    start_at: datetime | None = None,
    end_at: datetime | None = None,
    subject_type: str | None = None,
    subject_id: str | None = None,
    error_code: str | None = None,
) -> dict[str, Any]:
    return await runs.list_runs(
        context,
        after_id=cursor,
        limit=limit,
        state=state,
        agent_id=agent_id,
        key_id=key_id,
        purpose=purpose,
        start_at=start_at,
        end_at=end_at,
        subject_type=subject_type,
        subject_id=subject_id,
        error_code=error_code,
    )


@admin_router.post("/{run_id}/rerun", status_code=202)
async def rerun(
    run_id: str, body: RerunInput, key: Key, context: Context, runs: Runs
) -> AdmissionReceipt:
    return await runs.rerun(context, run_id, key, body.input)


@admin_router.get("/{run_id}/trace")
async def trace(
    run_id: str,
    context: Context,
    runs: Runs,
    after_sequence: Annotated[int, Query(ge=0)] = 0,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
) -> TracePage:
    return await runs.trace(context, run_id, after_sequence=after_sequence, limit=limit)


@admin_router.get("/{run_id}")
async def admin_get_run(run_id: str, context: Context, runs: Runs) -> ResultEnvelope:
    return await runs.get_run(context, run_id)


@admin_router.post("/{run_id}/cancel")
async def admin_cancel(run_id: str, context: Context, runs: Runs) -> AdmissionReceipt:
    return await runs.cancel(context, run_id)
