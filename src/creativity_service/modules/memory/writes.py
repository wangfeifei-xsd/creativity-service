"""候选、明确保存与确认统一走主体互斥及版本协议。"""

from typing import Any

from creativity_service.core.context import AuthContext
from creativity_service.core.database import UnitOfWork, transaction
from creativity_service.core.deletion import DeletionGuard
from creativity_service.core.primitives import ServiceError, digest, new_id, utcnow
from creativity_service.modules.memory import repositories as repo
from creativity_service.modules.memory.base import MemoryKernel
from creativity_service.modules.memory.ports import SourceState
from creativity_service.modules.memory.schemas import (
    MemoryAttribute,
    MemoryCreate,
    MemoryPolicy,
    PreferenceInput,
    PreferenceView,
)
from creativity_service.modules.memory.validation import expiry, validate_value


class MemoryWrites(MemoryKernel):
    async def persist(
        self,
        uow: UnitOfWork,
        context: AuthContext,
        body: MemoryCreate,
        policy: MemoryPolicy,
        source: dict[str, Any],
        state: SourceState,
        *,
        confirmed: bool,
        reason: str,
        target: dict[str, Any] | None = None,
        force: bool = False,
        archive: bool = False,
    ) -> dict[str, Any]:
        self.require_subject(context)
        await DeletionGuard(context.scope).check(uow, [])
        attribute = (
            MemoryAttribute(key=body.key, label="会话归档", value_schema={"type": "string"})
            if archive and body.memory_type == "ARCHIVE"
            else validate_value(
                body.key, body.memory_type, body.value, policy, await self.attributes(uow, context)
            )
        )
        if body.memory_type == "FACT" and body.key not in state.fact_keys:
            raise ServiceError(
                "MEMORY_AUTHORITY_REQUIRED", "稳定事实必须带对应属性的权威工具证据", 422
            )
        if state.observed_at > utcnow():
            raise ServiceError("MEMORY_SOURCE_INVALID", "来源观测时间不能晚于当前时间", 422)
        enabled = (await self.preference(uow, context, [])).enabled
        if not enabled:
            raise ServiceError("MEMORY_DISABLED", "主体已关闭长期记忆", 409)
        if policy.write_mode == "DISABLED":
            raise ServiceError("MEMORY_WRITE_DISABLED", "当前策略禁止保存记忆", 409)
        expires_at = expiry(policy, body.expires_at, state.observed_at)
        items = [
            await self.refresh(uow, context, r)
            for r in await repo.rows(uow.connection, "memories", context.scope)
        ]
        same_key = [
            r
            for r in items
            if r["key"] == body.key
            and r["status"] == "ACTIVE"
            and (not target or r["id"] != target["id"])
        ]
        if len(same_key) > 1:
            raise ServiceError("STORAGE_INVARIANT_BROKEN", "同一属性存在多个生效值", 503)
        active = same_key[0] if same_key else None
        activate = archive or confirmed and (force or policy.write_mode == "EXPLICIT")
        if activate and active and not force:
            rank = (confirmed, state.trust_level, state.observed_at)
            old_rank = (active["confirmed"], active["trust_level"], active["observed_at"])
            if body.value != active["value"] and rank < old_rank:
                activate, reason = False, "CONFLICT"
        if target is None:
            # 同值同状态追加独立依据；相同来源重试不新增条数或版本。
            target = next(
                (
                    r
                    for r in items
                    if r["key"] == body.key
                    and r["value"] == body.value
                    and r["status"] == ("ACTIVE" if activate else "PROPOSED")
                ),
                None,
            )
            if target:
                existing = await repo.one(
                    uow.connection,
                    "memory_sources",
                    context.scope,
                    memory_id=target["id"],
                    source_type=source["source_type"],
                    source_id=source["source_id"],
                    source_version=source["source_version"],
                    status="ACTIVE",
                )
                if existing:
                    return target
        if activate and active and (not target or target["id"] != active["id"]):
            await self.version(uow, context, active, "SUPERSEDED", status="SUPERSEDED")
        occupied = sum(r["status"] in {"ACTIVE", "PROPOSED"} for r in items)
        if target is None and occupied - int(activate and active is not None) >= policy.max_items:
            raise ServiceError("MEMORY_LIMIT_REACHED", "长期记忆条数已达上限，请先清理", 409)
        if target is None:
            target = await repo.save(
                uow,
                "memories",
                new_id("memory"),
                {
                    "source_mode": "ALL" if archive else "ANY",
                    "key": body.key,
                    "display_name": attribute.label,
                    "memory_type": body.memory_type,
                    "value": body.value,
                    "status": "PROPOSED",
                    "expires_at": expires_at,
                    "current_version_id": active["current_version_id"]
                    if active
                    else new_id("initial"),
                    "confirmed": confirmed,
                    "observed_at": state.observed_at,
                    "trust_level": state.trust_level,
                    "usage_count": 0,
                    "subject_name": await self.subject_name(uow, context),
                },
            )
        elif target["value"] != body.value:
            # 修正后原来源不能再为新值背书；来源图只保留新值的独立依据。
            await self.detach_sources(uow, context, target["id"])
        existing_source = await repo.one(
            uow.connection,
            "memory_sources",
            context.scope,
            memory_id=target["id"],
            source_type=source["source_type"],
            source_id=source["source_id"],
            source_version=source["source_version"],
            status="ACTIVE",
        )
        source_row = await repo.save(
            uow,
            "memory_sources",
            existing_source["id"] if existing_source else new_id("memory_source"),
            {
                **source,
                "memory_id": target["id"],
                "observed_at": state.observed_at,
                "authority": state.authority,
                "trust_level": state.trust_level,
                "status": "ACTIVE",
            },
        )
        await self.link(uow, context, source_row, target["id"])
        effective_sources = await repo.rows(
            uow.connection, "memory_sources", context.scope, memory_id=target["id"], status="ACTIVE"
        )
        return await self.version(
            uow,
            context,
            target,
            reason,
            value=body.value,
            status="ACTIVE" if activate else "PROPOSED",
            confirmed=confirmed,
            expires_at=expires_at,
            observed_at=max(s["observed_at"] for s in effective_sources),
            trust_level=max(s["trust_level"] for s in effective_sources),
        )

    @staticmethod
    async def subject_name(uow: UnitOfWork, context: AuthContext) -> str | None:
        from creativity_service.core.database import Repository
        from creativity_service.modules.conversations.tables import metadata

        rows = await Repository(metadata.tables["conversations"], context.scope).find(
            uow.connection
        )
        return next(
            (
                str(r["subject_name"])
                for r in rows
                if r["subject_name"] and r["status"] not in {"DELETING", "DELETED"}
            ),
            None,
        )

    @staticmethod
    def explicit_source(context: AuthContext) -> tuple[dict[str, Any], SourceState]:
        admin = context.principal_type == "management"
        return (
            {
                "source_type": "memory_input",
                "source_id": new_id("input"),
                "source_version": "1",
                "evidence_id": None,
            },
            SourceState(
                "人工修正" if admin else "用户明确输入",
                "ADMIN" if admin else "USER",
                3 if admin else 4,
                utcnow(),
            ),
        )

    @staticmethod
    async def detach_sources(uow: UnitOfWork, context: AuthContext, memory_id: str) -> None:
        from sqlalchemy import delete

        from creativity_service.core.database import Repository
        from creativity_service.core.database.tables import metadata

        for row in await repo.rows(
            uow.connection, "memory_sources", context.scope, memory_id=memory_id, status="ACTIVE"
        ):
            await repo.save(uow, "memory_sources", row["id"], {"status": "REVOKED"})
        table = metadata.tables["source_links"]
        await uow.connection.execute(
            delete(table).where(
                Repository(table, context.scope).predicate(),
                table.c.derived_type == "memory",
                table.c.derived_id == memory_id,
            )
        )

    async def set_preferences(
        self, context: AuthContext, body: PreferenceInput, anchor_id: str | None = None
    ) -> PreferenceView:
        context = await self.subject(context, anchor_id)
        await self.authorization.require(context, "memory:preferences", "scope")
        actions = await self.preference_actions(context)
        async with transaction(self.engine, context.scope, repo.keys(context.scope)) as uow:
            current = await self.preference(uow, context, [])
            if current.revision != body.revision:
                raise ServiceError("REVISION_CONFLICT", "偏好设置已变化，请刷新后重试", 409)
            await repo.save(
                uow,
                "memory_preferences",
                digest(["preferences", context.scope.model_dump()]),
                {"enabled": body.enabled, "changed_by": context.principal_id},
            )
            return await self.preference(uow, context, actions)
