"""主体身份只取认证委托；管理入口通过服务端已有记录恢复主体。"""

from typing import Annotated

from fastapi import APIRouter, Depends, Query, Request

from creativity_service.core.context import AuthContext, require_http_context
from creativity_service.core.primitives import unavailable
from creativity_service.modules.memory.schemas import (
    MemoryConfirm,
    MemoryCreate,
    MemoryDeletion,
    MemoryDetail,
    MemoryList,
    MemorySubject,
    MemoryUpdate,
    MemoryView,
    PolicyInput,
    PolicyView,
    PreferenceInput,
    PreferenceView,
)
from creativity_service.modules.memory.services import MemoryService

router = APIRouter(tags=["记忆管理"])
admin_router = APIRouter(tags=["记忆策略"])
Context = Annotated[AuthContext, Depends(require_http_context)]


def service(request: Request) -> MemoryService:
    value = getattr(request.app.state, "memory", None)
    if not isinstance(value, MemoryService):
        raise unavailable("记忆服务")
    return value


Memory = Annotated[MemoryService, Depends(service)]


@router.get("/memories")
async def list_memories(
    context: Context,
    memory: Memory,
    anchor_id: str | None = None,
    cursor: str | None = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 30,
    status: str | None = None,
    key: str | None = None,
) -> MemoryList:
    return await memory.list_memories(
        context, anchor_id=anchor_id, cursor=cursor, limit=limit, status=status, key=key
    )


@router.post("/memories", status_code=201)
async def create(
    body: MemoryCreate, context: Context, memory: Memory, anchor_id: str | None = None
) -> MemoryView:
    return await memory.create(context, body, anchor_id)


@router.post("/memories/clear", status_code=202)
async def clear(context: Context, memory: Memory, anchor_id: str | None = None) -> MemoryDeletion:
    return await memory.clear(context, anchor_id)


@router.get("/memories/{memory_id}")
async def detail(memory_id: str, context: Context, memory: Memory) -> MemoryDetail:
    return await memory.detail(context, memory_id)


@router.patch("/memories/{memory_id}")
async def update(
    memory_id: str, body: MemoryUpdate, context: Context, memory: Memory
) -> MemoryView:
    return await memory.update(context, memory_id, body)


@router.post("/memories/{memory_id}/confirm")
async def confirm(
    memory_id: str, body: MemoryConfirm, context: Context, memory: Memory
) -> MemoryView:
    return await memory.confirm(context, memory_id, body)


@router.delete("/memories/{memory_id}", status_code=202)
async def forget(memory_id: str, context: Context, memory: Memory) -> MemoryDeletion:
    return await memory.forget(context, memory_id)


@router.get("/memory-preferences")
async def preferences(
    context: Context, memory: Memory, anchor_id: str | None = None
) -> PreferenceView:
    return await memory.get_preferences(context, anchor_id)


@router.put("/memory-preferences")
async def set_preferences(
    body: PreferenceInput, context: Context, memory: Memory, anchor_id: str | None = None
) -> PreferenceView:
    return await memory.set_preferences(context, body, anchor_id)


@router.get("/memory-deletions/{deletion_id}")
async def deletion(deletion_id: str, context: Context, memory: Memory) -> MemoryDeletion:
    return await memory.deletion(context, deletion_id)


@admin_router.get("/memory-subjects")
async def subjects(context: Context, memory: Memory) -> list[MemorySubject]:
    return await memory.subjects(context)


@admin_router.get("/memory-policy")
async def policy(context: Context, memory: Memory, agent_id: str | None = None) -> PolicyView:
    return await memory.get_policy(context, agent_id)


@admin_router.put("/memory-policy")
async def set_policy(
    body: PolicyInput, context: Context, memory: Memory, agent_id: str | None = None
) -> PolicyView:
    return await memory.set_policy(context, body, agent_id)
