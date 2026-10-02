"""管理与业务显式写入共用记忆业务服务。"""

from creativity_service.core.context import AuthContext
from creativity_service.core.database import transaction
from creativity_service.core.primitives import ServiceError
from creativity_service.modules.memory import repositories as repo
from creativity_service.modules.memory.deletions import MemoryDeletions
from creativity_service.modules.memory.policies import MemoryPolicies
from creativity_service.modules.memory.runtime import MemoryRuntime
from creativity_service.modules.memory.schemas import (
    MemoryConfirm,
    MemoryCreate,
    MemoryUpdate,
    MemoryView,
)


class MemoryService(MemoryRuntime, MemoryDeletions, MemoryPolicies):
    async def create(
        self, context: AuthContext, body: MemoryCreate, anchor_id: str | None = None
    ) -> MemoryView:
        context = await self.subject(context, anchor_id)
        await self.authorization.require(context, "memory:write", "scope")
        await self.authorization.require(context, "memory:read", "scope")
        actions = await self.actions(context)
        source, state = self.explicit_source(context)
        async with transaction(self.engine, context.scope, repo.keys(context.scope)) as uow:
            row = await self.persist(
                uow,
                context,
                body,
                await self.policy(uow, context),
                source,
                state,
                confirmed=True,
                reason="CREATED",
            )
            return await self.view(uow, context, row, actions)

    async def update(self, context: AuthContext, memory_id: str, body: MemoryUpdate) -> MemoryView:
        context = await self.locate(context, memory_id)
        await self.authorization.require(context, "memory:write", memory_id)
        await self.authorization.require(context, "memory:read", memory_id)
        actions = await self.actions(context, memory_id)
        source, state = self.explicit_source(context)
        async with transaction(self.engine, context.scope, repo.keys(context.scope)) as uow:
            row = await repo.required(uow.connection, "memories", context.scope, id=memory_id)
            row = await self.refresh(uow, context, row)
            if row["status"] in {"REVOKED", "SUPERSEDED"}:
                raise ServiceError("MEMORY_NOT_EDITABLE", "已撤销或已替代的记忆不能重新启用", 409)
            if row["revision"] != body.revision:
                raise ServiceError("REVISION_CONFLICT", "记忆已变化，请刷新后重试", 409)
            created = await self.persist(
                uow,
                context,
                MemoryCreate(
                    key=row["key"],
                    memory_type=row["memory_type"],
                    value=body.value,
                    expires_at=body.expires_at,
                ),
                await self.policy(uow, context),
                source,
                state,
                confirmed=True,
                reason="CORRECTED",
                target=row,
                force=True,
            )
            return await self.view(uow, context, created, actions)

    async def confirm(
        self, context: AuthContext, memory_id: str, body: MemoryConfirm
    ) -> MemoryView:
        context = await self.locate(context, memory_id)
        await self.authorization.require(context, "memory:write", memory_id)
        await self.authorization.require(context, "memory:read", memory_id)
        actions = await self.actions(context, memory_id)
        async with transaction(self.engine, context.scope, repo.keys(context.scope)) as uow:
            row = await repo.required(uow.connection, "memories", context.scope, id=memory_id)
            row = await self.refresh(uow, context, row)
            if row["revision"] != body.revision:
                raise ServiceError("REVISION_CONFLICT", "记忆已变化，请刷新后重试", 409)
            if row["status"] != "PROPOSED":
                raise ServiceError("MEMORY_NOT_CONFIRMABLE", "只有待确认记忆可以确认", 409)
            if row["memory_type"] == "FACT":
                sources = await repo.rows(
                    uow.connection,
                    "memory_sources",
                    context.scope,
                    memory_id=memory_id,
                    status="ACTIVE",
                )
                existing = sources[0]
                state = await self.source_state(uow, context, existing)
                if state is None:
                    raise ServiceError("MEMORY_SOURCE_INVALID", "事实来源已失效", 409)
                source = {
                    k: existing[k]
                    for k in ("source_type", "source_id", "source_version", "evidence_id")
                }
            else:
                source, state = self.explicit_source(context)
            created = await self.persist(
                uow,
                context,
                MemoryCreate(
                    key=row["key"],
                    memory_type=row["memory_type"],
                    value=row["value"],
                    expires_at=row["expires_at"],
                ),
                await self.policy(uow, context),
                source,
                state,
                confirmed=True,
                reason="CONFIRMED",
                target=row,
                force=True,
            )
            return await self.view(uow, context, created, actions)
