"""业务与管理会话路由共用服务，身份及数据范围来自认证上下文。"""

from datetime import datetime
from typing import Annotated
from urllib.parse import quote

from fastapi import APIRouter, Depends, Query, Request, Response

from creativity_service.core.artifacts import MAX_FILE_BYTES
from creativity_service.core.context import AuthContext, require_http_context
from creativity_service.core.contracts import Artifact, ResultEnvelope
from creativity_service.core.primitives import ServiceError, unavailable
from creativity_service.modules.conversations.schemas import (
    BranchInput,
    ConversationCreate,
    ConversationDetail,
    ConversationList,
    ConversationView,
    DeletionImpact,
    DeletionView,
    MessageInput,
    MessagePage,
    MessageReceipt,
    TitleInput,
)
from creativity_service.modules.conversations.services import ConversationService
from creativity_service.modules.runs.schemas import AdmissionReceipt

router = APIRouter(tags=["会话管理"])
Context = Annotated[AuthContext, Depends(require_http_context)]


def service(request: Request) -> ConversationService:
    value = getattr(request.app.state, "conversations", None)
    if not isinstance(value, ConversationService):
        raise unavailable("会话服务")
    return value


Conversations = Annotated[ConversationService, Depends(service)]


@router.post("/conversations/{conversation_id}/branches", status_code=201)
async def branch(
    conversation_id: str, body: BranchInput, context: Context, conversations: Conversations
) -> ConversationView:
    return await conversations.branch(context, conversation_id, body)


@router.get("/conversations")
async def list_conversations(
    context: Context,
    conversations: Conversations,
    cursor: str | None = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 30,
    status: str | None = None,
    agent_id: str | None = None,
    subject: str | None = None,
    environment: str | None = None,
    start_at: datetime | None = None,
    end_at: datetime | None = None,
) -> ConversationList:
    return await conversations.list_conversations(
        context,
        cursor=cursor,
        limit=limit,
        status=status,
        agent_id=agent_id,
        subject=subject,
        environment=environment,
        start_at=start_at,
        end_at=end_at,
    )


@router.post("/conversations", status_code=201)
async def create(
    body: ConversationCreate, context: Context, conversations: Conversations
) -> ConversationView:
    return await conversations.create(context, body)


@router.get("/conversations/{conversation_id}")
async def detail(
    conversation_id: str, context: Context, conversations: Conversations
) -> ConversationDetail:
    return await conversations.detail(context, conversation_id)


@router.patch("/conversations/{conversation_id}")
async def title(
    conversation_id: str, body: TitleInput, context: Context, conversations: Conversations
) -> ConversationView:
    return await conversations.title(context, conversation_id, body)


@router.get("/conversations/{conversation_id}/messages")
async def messages(
    conversation_id: str,
    context: Context,
    conversations: Conversations,
    cursor: str | None = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
) -> MessagePage:
    return await conversations.messages(context, conversation_id, cursor=cursor, limit=limit)


@router.post("/conversations/{conversation_id}/messages", status_code=202)
async def submit(
    conversation_id: str, body: MessageInput, context: Context, conversations: Conversations
) -> MessageReceipt:
    return await conversations.submit(context, conversation_id, body)


@router.post("/conversations/{conversation_id}/archive")
async def archive(
    conversation_id: str, context: Context, conversations: Conversations
) -> ConversationView:
    return await conversations.archive(context, conversation_id)


@router.post("/conversations/{conversation_id}/restore")
async def restore(
    conversation_id: str, context: Context, conversations: Conversations
) -> ConversationView:
    return await conversations.archive(context, conversation_id, restore=True)


@router.post("/conversations/{conversation_id}/runs/{run_id}/cancel")
async def cancel(
    conversation_id: str, run_id: str, context: Context, conversations: Conversations
) -> AdmissionReceipt:
    return await conversations.cancel(context, conversation_id, run_id)


@router.get("/conversations/{conversation_id}/runs/{run_id}")
async def run_detail(
    conversation_id: str, run_id: str, context: Context, conversations: Conversations
) -> ResultEnvelope:
    return await conversations.run_detail(context, conversation_id, run_id)


@router.get("/conversations/{conversation_id}/deletion-preview")
async def preview(
    conversation_id: str, context: Context, conversations: Conversations
) -> DeletionImpact:
    return await conversations.preview_delete(context, conversation_id)


@router.delete("/conversations/{conversation_id}", status_code=202)
async def delete(
    conversation_id: str, context: Context, conversations: Conversations
) -> DeletionView:
    return await conversations.delete(context, conversation_id)


@router.get("/deletions/{deletion_id}")
async def deletion(
    deletion_id: str, context: Context, conversations: Conversations
) -> DeletionView:
    return await conversations.deletion(context, deletion_id)


@router.post(
    "/conversations/{conversation_id}/attachments",
    status_code=201,
    openapi_extra={
        "requestBody": {
            "required": True,
            "description": "原始文件字节，最多 20 MB；委托摘要绑定实际字节，非 multipart",
            "content": {
                "application/octet-stream": {"schema": {"type": "string", "format": "binary"}}
            },
        }
    },
)
async def upload(
    conversation_id: str,
    request: Request,
    context: Context,
    conversations: Conversations,
    name: Annotated[str, Query(min_length=1, max_length=255)],
) -> Artifact:
    data = bytearray()
    async for chunk in request.stream():
        if len(data) + len(chunk) > MAX_FILE_BYTES:
            raise ServiceError("ARTIFACT_SIZE_INVALID", "附件不能超过 20 MB", 422)
        data.extend(chunk)
    return await conversations.upload(
        context,
        conversation_id,
        name,
        request.headers.get("content-type", "application/octet-stream"),
        bytes(data),
    )


@router.get(
    "/conversations/{conversation_id}/attachments/{artifact_id}/content",
    response_class=Response,
    responses={
        200: {
            "description": "文件字节，Content-Type 为文件实际类型，下载重新检查当前授权",
            "content": {
                "application/octet-stream": {"schema": {"type": "string", "format": "binary"}}
            },
        }
    },
)
async def download(
    conversation_id: str, artifact_id: str, context: Context, conversations: Conversations
) -> Response:
    data, content_type, name = await conversations.download(context, conversation_id, artifact_id)
    return Response(
        data,
        media_type=content_type,
        headers={
            "Content-Disposition": f"attachment; filename*=UTF-8''{quote(name, safe='')}",
            "Cache-Control": "private, no-store",
            "X-Content-Type-Options": "nosniff",
        },
    )


@router.post("/conversations/{conversation_id}/exports", status_code=201)
async def export(conversation_id: str, context: Context, conversations: Conversations) -> Artifact:
    return await conversations.export(context, conversation_id)
