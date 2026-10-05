"""基础错误契约及字段错误转换。"""

from collections.abc import Mapping
from typing import cast

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from pydantic import BaseModel, Field
from sqlalchemy.exc import SQLAlchemyError
from starlette.exceptions import HTTPException
from starlette.responses import JSONResponse

from creativity_service.core.observability import request_id_context
from creativity_service.core.primitives import ServiceError


class FieldError(BaseModel):
    path: list[str | int] = Field(description="表单字段路径，不包含传输位置前缀")
    message: str = Field(description="可供界面展示的中文错误说明")


class ErrorDetail(BaseModel):
    code: str = Field(description="供程序处理的稳定错误标识")
    message: str = Field(description="可供界面展示的中文错误说明")
    fields: list[FieldError] = Field(default_factory=list, description="字段错误列表")


class ErrorResponse(BaseModel):
    error: ErrorDetail
    request_id: str = Field(description="对应响应头 X-Request-ID 的请求标识")


def error_response(
    status: int,
    code: str,
    message: str,
    fields: list[FieldError] | None = None,
    headers: Mapping[str, str] | None = None,
) -> JSONResponse:
    body = ErrorResponse(
        error=ErrorDetail(code=code, message=message, fields=fields or []),
        request_id=request_id_context.get() or "",
    )
    return JSONResponse(status_code=status, content=body.model_dump(), headers=headers)


def register_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(SQLAlchemyError)
    async def database_error(_request: Request, _exc: SQLAlchemyError) -> JSONResponse:
        return error_response(503, "DEPENDENCY_UNAVAILABLE", "数据存储暂不可用")

    @app.exception_handler(ServiceError)
    async def service_error(_request: Request, exc: ServiceError) -> JSONResponse:
        return error_response(
            exc.status,
            exc.code,
            exc.message,
            [FieldError.model_validate(field) for field in getattr(exc, "fields", [])],
        )

    @app.exception_handler(RequestValidationError)
    async def validation_error(_request: Request, exc: RequestValidationError) -> JSONResponse:
        messages = {
            "missing": "请填写此项",
            "extra_forbidden": "不支持此字段",
            "json_invalid": "请求内容不是有效的 JSON",
        }
        fields = []
        for item in exc.errors():
            message = messages.get(item["type"], "字段值不符合要求")
            context = item.get("ctx", {})
            # 密码等秘密字符串使用通用长度错误类型，仅读取限制，不回显输入值。
            if isinstance(item.get("input"), str):
                if item["type"] in {"string_too_short", "too_short"}:
                    message = f"至少输入 {context['min_length']} 个字符"
                elif item["type"] in {"string_too_long", "too_long"}:
                    message = f"最多输入 {context['max_length']} 个字符"
            fields.append(
                FieldError(path=cast(list[str | int], list(item["loc"])[1:]), message=message)
            )
        return error_response(422, "VALIDATION_ERROR", "请检查填写内容", fields)

    @app.exception_handler(HTTPException)
    async def http_error(_request: Request, exc: HTTPException) -> JSONResponse:
        codes = {
            400: ("BAD_REQUEST", "请求内容不正确"),
            401: ("UNAUTHENTICATED", "请重新登录"),
            403: ("FORBIDDEN", "无权执行此操作"),
            404: ("NOT_FOUND", "请求资源不存在"),
            405: ("METHOD_NOT_ALLOWED", "不支持此请求方式"),
            409: ("CONFLICT", "数据已变更，请刷新后重试"),
            429: ("RATE_LIMITED", "请求过于频繁，请稍后重试"),
            503: ("SERVICE_UNAVAILABLE", "服务暂不可用，请稍后重试"),
        }
        code, message = codes.get(exc.status_code, ("REQUEST_FAILED", "请求失败"))
        return error_response(exc.status_code, code, message, headers=exc.headers)
