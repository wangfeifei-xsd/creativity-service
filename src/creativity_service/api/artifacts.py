"""通用上传返回稳定 URL；历史受控产物保留原下载接口。"""

from collections.abc import AsyncGenerator
from typing import Annotated
from urllib.parse import quote

from fastapi import APIRouter, Depends, Request, Response
from starlette.datastructures import UploadFile
from starlette.formparsers import MultiPartException, MultiPartParser

from creativity_service.core.artifacts import MAX_FILE_BYTES
from creativity_service.core.context import AuthContext, require_http_context
from creativity_service.core.files import UploadedFile
from creativity_service.core.primitives import ServiceError, unavailable
from creativity_service.core.services import CoreServices

router = APIRouter(tags=["受控文件"])


@router.post(
    "/files/upload",
    status_code=201,
    tags=["通用文件"],
    openapi_extra={
        "requestBody": {
            "required": True,
            "content": {
                "multipart/form-data": {
                    "schema": {
                        "type": "object",
                        "required": ["file"],
                        "properties": {"file": {"type": "string", "format": "binary"}},
                        "additionalProperties": False,
                    }
                }
            },
        }
    },
)
async def upload_file(
    request: Request,
    context: Annotated[AuthContext, Depends(require_http_context)],
) -> UploadedFile:
    services: CoreServices | None = getattr(request.app.state, "core", None)
    if services is None:
        raise unavailable("文件服务")

    # 先认证再解析；连同 multipart 开销限制总请求体，避免无限写入暂存文件。
    async def bounded_body() -> AsyncGenerator[bytes, None]:
        size = 0
        async for chunk in request.stream():
            size += len(chunk)
            if size > MAX_FILE_BYTES + 64 * 1024:
                raise MultiPartException("文件不能超过 20 MiB")
            yield chunk

    try:
        form = await MultiPartParser(
            request.headers, bounded_body(), max_files=1, max_fields=0
        ).parse()
    except MultiPartException as exc:
        raise ServiceError("FILE_INVALID", "请提交一个不超过 20 MiB 的文件", 422) from exc
    try:
        file = form.get("file")
        if not isinstance(file, UploadFile):
            raise ServiceError("FILE_REQUIRED", "请选择待上传文件", 422)
        data = await file.read(MAX_FILE_BYTES + 1)
        if len(data) > MAX_FILE_BYTES:
            raise ServiceError("FILE_SIZE_INVALID", "文件不能超过 20 MiB", 422)
        return await services.files.upload(
            context, file.filename or "", file.content_type or "application/octet-stream", data
        )
    finally:
        await form.close()


@router.get(
    "/artifacts/{artifact_id}/content",
    response_class=Response,
    responses={
        200: {
            "content": {
                "application/octet-stream": {"schema": {"type": "string", "format": "binary"}}
            }
        }
    },
)
async def download_artifact(
    artifact_id: str,
    request: Request,
    context: Annotated[AuthContext, Depends(require_http_context)],
) -> Response:
    services: CoreServices | None = getattr(request.app.state, "core", None)
    if services is None:
        raise unavailable("文件服务")
    data, content_type, name = await services.artifacts.download(context, artifact_id)
    return Response(
        content=data,
        media_type=content_type,
        headers={
            "Content-Disposition": f"attachment; filename*=UTF-8''{quote(name, safe='')}",
            "Cache-Control": "private, no-store",
            "X-Content-Type-Options": "nosniff",
        },
    )
