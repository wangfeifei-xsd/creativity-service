"""文件通过服务端返回；每次请求重新检查认证、授权和删除标记。"""

from typing import Annotated
from urllib.parse import quote

from fastapi import APIRouter, Depends, Request, Response

from creativity_service.core.context import AuthContext, require_http_context
from creativity_service.core.primitives import unavailable
from creativity_service.core.services import CoreServices

router = APIRouter(tags=["受控文件"])


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
