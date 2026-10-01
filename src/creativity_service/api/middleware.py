"""为成功及失败响应统一添加请求标识。"""

import logging
from time import perf_counter
from uuid import uuid4

from starlette.datastructures import MutableHeaders
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from creativity_service.api.errors import error_response
from creativity_service.core.observability import request_id_context

logger = logging.getLogger(__name__)


class RequestContextMiddleware:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        request_id = uuid4().hex
        scope.setdefault("state", {})["request_id"] = request_id
        token = request_id_context.set(request_id)
        started = perf_counter()
        status_code = 500
        response_started = False

        async def send_with_context(message: Message) -> None:
            nonlocal status_code, response_started
            if message["type"] == "http.response.start":
                response_started = True
                status_code = message["status"]
                headers = MutableHeaders(scope=message)
                headers["X-Request-ID"] = request_id
            await send(message)

        try:
            await self.app(scope, receive, send_with_context)
        except Exception as exc:
            logger.error("请求处理失败", extra={"error_type": type(exc).__name__})
            if response_started:
                raise
            await error_response(500, "INTERNAL_ERROR", "服务处理失败，请稍后重试")(
                scope, receive, send_with_context
            )
        finally:
            logger.info(
                "请求处理完成",
                extra={
                    "method": scope["method"],
                    "route": getattr(scope.get("route"), "path", "未匹配路由"),
                    "status_code": status_code,
                    "duration_ms": round((perf_counter() - started) * 1000, 2),
                },
            )
            request_id_context.reset(token)
