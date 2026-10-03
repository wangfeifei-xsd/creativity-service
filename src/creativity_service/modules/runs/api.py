"""方案 17 注入正式解析器后登记此路由；正文没有可上传执行定义的入口。"""

import asyncio
from datetime import datetime
from time import monotonic
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Header, Query, Request, Response
from starlette.responses import StreamingResponse

from creativity_service.api.errors import ErrorResponse
from creativity_service.core.context import AuthContext, require_http_context
from creativity_service.core.contracts import ResultEnvelope
from creativity_service.modules.runs.assembly import require_runs
from creativity_service.modules.runs.interruptions import InterruptionView, ResumeInput
from creativity_service.modules.runs.schemas import (
    TERMINAL,
    AdmissionReceipt,
    RerunInput,
    RunDetail,
    RunFilterOptions,
    RunList,
    RunRequest,
    TracePage,
)
from creativity_service.modules.runs.services import RunService
from creativity_service.modules.runtime.stream import event_stream

router = APIRouter(prefix="/runs", tags=["任务运行"])
admin_router = APIRouter(prefix="/runs", tags=["运行管理"])
Context = Annotated[AuthContext, Depends(require_http_context)]
Key = Annotated[str, Header(alias="Idempotency-Key", min_length=1, max_length=128)]


def service(request: Request) -> RunService:
    return require_runs(getattr(request.app.state, "runs", None))


Runs = Annotated[RunService, Depends(service)]


@router.get("/{run_id}/interruption")
@admin_router.get("/{run_id}/interruption")
async def interruption(run_id: str, context: Context, runs: Runs) -> InterruptionView | None:
    return await runs.interruption(context, run_id)


@router.post("/{run_id}/resume")
@admin_router.post("/{run_id}/resume")
async def resume(run_id: str, body: ResumeInput, context: Context, runs: Runs) -> AdmissionReceipt:
    return await runs.resume(context, run_id, body)


@router.post(
    "",
    status_code=202,
    response_model=None,
    summary="创建统一运行",
    description="sync 最多等待 15 秒；窗口内终结返回 200，否则返回原运行的 202。"
    "async/stream 返回 202。相同逻辑请求重发沿用原幂等键，每次均重新授权。",
    responses={
        200: {"model": ResultEnvelope, "description": "同步窗口内已终结，须检查 state 和 error"},
        202: {"model": AdmissionReceipt, "description": "已受理，使用原 run_id 查询或订阅"},
    },
)
async def admit(
    body: RunRequest, key: Key, context: Context, runs: Runs, request: Request, response: Response
) -> AdmissionReceipt | ResultEnvelope:
    receipt = await runs.admit_run(context, body, key)
    if body.delivery == "sync":
        until = monotonic() + min(15, float(getattr(request.app.state, "sync_wait_seconds", 15)))
        while True:
            result = await runs.get_run(context, receipt.run_id)
            if result.state in TERMINAL:
                response.status_code = 200
                return result
            if monotonic() >= until:
                break
            await asyncio.sleep(min(0.1, max(0, until - monotonic())))
    return receipt


@router.get(
    "/{run_id}/events",
    response_class=StreamingResponse,
    responses={
        200: {"content": {"text/event-stream": {}}},
        410: {"model": ErrorResponse, "description": "事件已过期，查询原运行快照"},
    },
)
@admin_router.get(
    "/{run_id}/events",
    response_class=StreamingResponse,
    responses={
        200: {"content": {"text/event-stream": {}}},
        410: {"model": ErrorResponse, "description": "事件已过期，查询原运行快照"},
    },
)
async def events(
    run_id: str,
    context: Context,
    runs: Runs,
    request: Request,
    after_sequence: Annotated[int, Query(ge=0)] = 0,
    _last_event_id: Annotated[
        str | None,
        Header(alias="Last-Event-ID", description="最后已处理的事件序号；优先于 after_sequence"),
    ] = None,
) -> StreamingResponse:
    return await event_stream(request, context, runs, run_id, after_sequence)


@router.get("/{run_id}")
async def get_run(run_id: str, context: Context, runs: Runs) -> ResultEnvelope:
    return await runs.get_run(context, run_id)


@router.post("/{run_id}/cancel")
async def cancel(run_id: str, context: Context, runs: Runs) -> AdmissionReceipt:
    return await runs.cancel(context, run_id)


@admin_router.get("", response_model=RunList)
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


@admin_router.get("/options", response_model=RunFilterOptions)
async def options(context: Context, runs: Runs) -> dict[str, Any]:
    return await runs.filter_options(context)


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


@admin_router.get("/{run_id}/detail", response_model=RunDetail)
async def detail(run_id: str, context: Context, runs: Runs) -> dict[str, Any]:
    return await runs.detail(context, run_id)


@admin_router.post("/{run_id}/cancel")
async def admin_cancel(run_id: str, context: Context, runs: Runs) -> AdmissionReceipt:
    return await runs.cancel(context, run_id)
