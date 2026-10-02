"""带 Authorization 的 SSE，数据库事件序号是唯一重连游标。"""

import asyncio
import json
from collections.abc import AsyncIterator
from time import monotonic

from fastapi import Request
from starlette.responses import StreamingResponse

from creativity_service.core.context import AuthContext, TaskEnvelope
from creativity_service.core.primitives import ServiceError
from creativity_service.modules.runs.schemas import TERMINAL
from creativity_service.modules.runs.services import RunService


def frame(event: str, payload: object, sequence: int | None = None) -> str:
    prefix = f"id: {sequence}\n" if sequence is not None else ""
    return (
        prefix
        + f"event: {event}\ndata: "
        + json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
        + "\n\n"
    )


async def event_stream(
    request: Request, context: AuthContext, runs: RunService, run_id: str, after_sequence: int = 0
) -> StreamingResponse:
    if "token" in request.query_params or "access_token" in request.query_params:
        raise ServiceError("TOKEN_IN_URL", "请通过 Authorization 请求头认证", 400)
    cursor = request.headers.get("Last-Event-ID")
    if cursor is not None:
        if len(cursor) > 20 or not cursor.isascii() or not cursor.isdigit():
            raise ServiceError("CURSOR_INVALID", "事件游标无效", 422)
        after_sequence = int(cursor)
    # 首次检查在响应头发送前完成，失效身份返回正常 HTTP 错误。
    await runs.events(context, run_id, after_sequence=after_sequence, limit=1)
    heartbeat_seconds = float(getattr(request.app.state, "sse_heartbeat_seconds", 15))
    poll_seconds = float(getattr(request.app.state, "sse_poll_seconds", 0.5))

    async def generate() -> AsyncIterator[str]:
        sequence = after_sequence
        heartbeat = monotonic()
        while not await request.is_disconnected():
            try:
                # 每条事件重新读取并鉴权，不能将旧批次在撤权或删除后继续发送。
                events = await runs.events(context, run_id, after_sequence=sequence, limit=1)
                if events:
                    event = events[0]
                    sequence = event.sequence
                    yield frame(event.event_type, event.model_dump(mode="json"), sequence)
                    if event.event_type == "completed":
                        return
                    continue
                row = await runs.load(
                    TaskEnvelope(channel_id=context.scope.channel_id, run_id=run_id)
                )
                if row["state"] in TERMINAL:
                    return
                if monotonic() - heartbeat >= heartbeat_seconds:
                    await runs.authorization.require(
                        await runs.access_context(context, run_id), "run:content", run_id
                    )
                    yield ": heartbeat\n\n"
                    heartbeat = monotonic()
            except ServiceError as exc:
                code = "AUTH_EXPIRED" if exc.status == 401 else exc.code
                yield frame(
                    "control",
                    {
                        "code": code,
                        "message": exc.message,
                        "status": exc.status,
                        "snapshot_path": str(request.url.path).removesuffix("/events"),
                    },
                )
                return
            await asyncio.sleep(poll_seconds)

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"},
    )
