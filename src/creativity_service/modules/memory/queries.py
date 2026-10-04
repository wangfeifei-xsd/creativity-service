"""管理列表与主体详情复用运行前的来源复核，不返回已删除版本的原文。"""

from datetime import datetime
from typing import Any

from sqlalchemy import and_, or_, select

from creativity_service.core.context import AuthContext
from creativity_service.core.contracts import VisibleAction
from creativity_service.core.database import UnitOfWork, transaction
from creativity_service.core.primitives import ServiceError, digest, utcnow
from creativity_service.modules.conversations.queries import decode_cursor, encode_cursor
from creativity_service.modules.conversations.tables import metadata as conversations
from creativity_service.modules.iam.reading import (
    read_actions,
    read_policy,
    require_action,
    resource_state,
)
from creativity_service.modules.memory import repositories as repo
from creativity_service.modules.memory.base import MemoryKernel
from creativity_service.modules.memory.reading import (
    MemoryReadData,
    indexed,
    scoped_id,
    scoped_rows,
)
from creativity_service.modules.memory.schemas import (
    REASONS,
    STATES,
    TYPES,
    MemoryDetail,
    MemoryList,
    MemorySubject,
    MemoryVersionView,
    MemoryView,
    PreferenceView,
)
from creativity_service.modules.memory.sources import DatabaseSourceReader
from creativity_service.modules.memory.tables import metadata
from creativity_service.modules.memory.validation import value_label


class MemoryQueries(MemoryKernel):
    async def view(
        self,
        uow: UnitOfWork,
        context: AuthContext,
        row: dict[str, Any],
        actions: list[Any],
        visible_sources: frozenset[str] = frozenset(),
        data: MemoryReadData | None = None,
    ) -> MemoryView:
        version = (
            data.versions.get(scoped_id(row, row["current_version_id"]))
            if data is not None
            else None
        )
        version = version or await repo.required(
            uow.connection, "memory_versions", context.scope, id=row["current_version_id"]
        )
        allowed = {"delete"}
        if row["memory_type"] != "ARCHIVE" and row["status"] in {"ACTIVE", "PROPOSED", "EXPIRED"}:
            allowed.add("edit")
        if row["status"] == "PROPOSED":
            allowed.add("confirm")
        if row["status"] == "REVOKED":
            allowed.clear()
        return MemoryView(
            memory_id=row["id"],
            layer="archive" if row["memory_type"] == "ARCHIVE" else "profile",
            layer_label="归档" if row["memory_type"] == "ARCHIVE" else "人物画像",
            key=row["key"],
            display_name=row["display_name"],
            memory_type=row["memory_type"],
            type_label=TYPES[row["memory_type"]],
            value=row["value"],
            value_label=value_label(row["key"], row["value"]),
            status=row["status"],
            status_label=STATES[row["status"]],
            confirmed=row["confirmed"],
            expires_at=row["expires_at"],
            revision=row["revision"],
            version_id=row["current_version_id"],
            version=version["version_number"],
            subject_name=row["subject_name"],
            usage_count=row["usage_count"],
            created_at=row["created_at"],
            sources=await self.source_views(uow, context, row, visible_sources, data),
            actions=[a for a in actions if a.action_key in allowed],
        )

    async def detail(self, context: AuthContext, memory_id: str) -> MemoryDetail:
        context = await self.locate(context, memory_id)
        policy = await read_policy(self.authorization, context)
        permissions = await read_actions(
            self.authorization,
            context,
            "memory",
            memory_id,
            ["memory:read", "memory:write", "memory:delete"],
            policy=policy,
            state=resource_state(context, "memory", {"id": memory_id}),
        )
        require_action(permissions, "memory:read")
        scope_permissions = await read_actions(
            self.authorization,
            context,
            "memory",
            "scope",
            ["memory:preferences", "memory:delete"],
            policy=policy,
        )
        actions = await self.actions(context, memory_id, permissions)
        preferences = await self.preference_actions(context, scope_permissions)
        visible_sources = (
            await self.visible_sources(context, memory_id) if policy is None else frozenset()
        )
        async with transaction(self.engine, context.scope, repo.keys(context.scope)) as uow:
            row = await repo.required(uow.connection, "memories", context.scope, id=memory_id)
            data = (
                await MemoryReadData.load(uow, context, [(context, row)], policy)
                if isinstance(self.sources, DatabaseSourceReader)
                else None
            )
            if data is not None and policy is not None:
                visible_sources = data.visible.get(scoped_id(row), frozenset())
            row = await self.refresh(uow, context, row, data)
            versions = await repo.rows(
                uow.connection, "memory_versions", context.scope, memory_id=memory_id
            )
            return MemoryDetail(
                memory=await self.view(uow, context, row, actions, visible_sources, data),
                versions=[
                    MemoryVersionView(
                        version=v["version_number"],
                        status_label=STATES[v["status"]],
                        reason=REASONS[v["reason"]],
                        changed_at=v["created_at"],
                    )
                    for v in sorted(versions, key=lambda v: v["version_number"], reverse=True)
                ],
                preferences=await self.preference(uow, context, preferences),
            )

    async def get_preferences(
        self, context: AuthContext, anchor_id: str | None = None
    ) -> PreferenceView:
        context = await self.subject(context, anchor_id)
        policy = await read_policy(self.authorization, context)
        permissions = await read_actions(
            self.authorization,
            context,
            "memory",
            "scope",
            ["memory:read", "memory:preferences", "memory:delete"],
            policy=policy,
        )
        require_action(permissions, "memory:read")
        actions = await self.preference_actions(context, permissions)
        async with transaction(self.engine, context.scope, repo.keys(context.scope)) as uow:
            return await self.preference(uow, context, actions)

    async def subjects(self, context: AuthContext) -> list[MemorySubject]:
        policy = await read_policy(self.authorization, context)
        permissions = await read_actions(
            self.authorization, context, "memory", "scope", ["memory:read"], policy=policy
        )
        require_action(permissions, "memory:read")
        scope = context.scope.model_dump()
        if context.principal_type == "management" and not context.scope.subject_id:
            scope.pop("subject_id")
            scope.pop("subject_type")
        found: dict[str, MemorySubject] = {}
        for table in (metadata.tables["memories"], conversations.tables["conversations"]):
            async with self.engine.connect() as connection:
                rows = (
                    (
                        await connection.execute(
                            select(table)
                            .where(
                                *(table.c[k] == v for k, v in scope.items()),
                                table.c.subject_id.is_not(None),
                            )
                            .order_by(table.c.created_at.desc())
                            .limit(500)
                        )
                    )
                    .mappings()
                    .all()
                )
            for row in rows:
                if row["status"] in {"DELETING", "DELETED", "REVOKED"}:
                    continue
                scoped = self.row_context(context, dict(row))
                key = digest(scoped.scope.model_dump())
                if key in found:
                    continue
                permissions = await read_actions(
                    self.authorization, scoped, "memory", "scope", ["memory:read"], policy=policy
                )
                if "memory:read" not in permissions:
                    continue
                label = row["subject_name"]
                if not label:
                    label = "主体名称不可用"
                found.setdefault(
                    digest(scoped.scope.model_dump()),
                    MemorySubject(anchor_id=row["id"], label=label),
                )
        return list(found.values())

    async def list_memories(
        self,
        context: AuthContext,
        *,
        anchor_id: str | None = None,
        cursor: str | None = None,
        limit: int = 30,
        status: str | None = None,
        key: str | None = None,
        layer: str | None = None,
    ) -> MemoryList:
        if (
            not 1 <= limit <= 200
            or status not in {None, *STATES}
            or layer not in {None, "archive", "profile"}
        ):
            raise ServiceError("MEMORY_FILTER_INVALID", "记忆筛选条件不正确", 422)
        if anchor_id:
            context = await self.subject(context, anchor_id)
        if context.principal_type != "management":
            self.require_subject(context)
        policy = await read_policy(self.authorization, context)
        permissions = await read_actions(
            self.authorization,
            context,
            "memory",
            "scope",
            ["memory:read", "memory:write", "memory:delete"],
            policy=policy,
        )
        require_action(permissions, "memory:read")
        actions = await self.actions(context, permissions=permissions)
        channel_permissions = await read_actions(
            self.authorization,
            context,
            "channel",
            context.scope.channel_id,
            ["channel:manage"],
            policy=policy,
        )
        if "channel:manage" in channel_permissions:
            actions.append(VisibleAction(action_key="policy", label="渠道策略"))
        binding = digest([context.scope.model_dump(), context.principal_id, status, key, layer])
        parsed = decode_cursor(cursor, binding)
        try:
            ceiling = datetime.fromisoformat(parsed["ceiling"]) if parsed else utcnow()
            after = (datetime.fromisoformat(parsed["time"]), str(parsed["id"])) if parsed else None
            if ceiling.tzinfo is None or (after and after[0].tzinfo is None):
                raise ValueError()
        except (KeyError, TypeError, ValueError) as exc:
            raise ServiceError("CURSOR_INVALID", "分页游标已失效，请刷新列表", 422) from exc
        table = metadata.tables["memories"]
        scope = context.scope.model_dump()
        if context.principal_type == "management" and not context.scope.subject_id:
            scope.pop("subject_id")
            scope.pop("subject_type")
        items: list[MemoryView] = []
        while len(items) <= limit:
            predicates = [table.c[k] == v for k, v in scope.items()]
            predicates.append(table.c.created_at <= ceiling)
            if after:
                predicates.append(
                    or_(
                        table.c.created_at < after[0],
                        and_(table.c.created_at == after[0], table.c.id < after[1]),
                    )
                )
            if layer:
                predicates.append(
                    table.c.memory_type == "ARCHIVE"
                    if layer == "archive"
                    else table.c.memory_type != "ARCHIVE"
                )
            if key:
                predicates.append(table.c.key == key)
            async with self.engine.connect() as connection:
                rows = (
                    (
                        await connection.execute(
                            select(table)
                            .where(*predicates)
                            .order_by(table.c.created_at.desc(), table.c.id.desc())
                            .limit(limit + 1)
                        )
                    )
                    .mappings()
                    .all()
                )
            if not rows:
                break
            candidates = []
            for item in rows:
                scoped = self.row_context(context, dict(item))
                permissions = await read_actions(
                    self.authorization,
                    scoped,
                    "memory",
                    item["id"],
                    ["memory:read", "memory:write", "memory:delete"],
                    policy=policy,
                    state=resource_state(scoped, "memory", item),
                )
                if "memory:read" in permissions:
                    visible_sources = (
                        await self.visible_sources(scoped, item["id"])
                        if policy is None
                        else frozenset()
                    )
                    candidates.append(
                        (
                            scoped,
                            dict(item),
                            await self.actions(scoped, item["id"], permissions),
                            visible_sources,
                        )
                    )
            after = (rows[-1]["created_at"], rows[-1]["id"])
            if candidates:
                keys = [key for scoped, _, _, _ in candidates for key in repo.keys(scoped.scope)]
                async with transaction(self.engine, context.scope, keys) as uow:
                    current = indexed(
                        await scoped_rows(
                            uow.connection, table, [(c, r["id"]) for c, r, _, _ in candidates]
                        )
                    )
                    records = [
                        (c, current[scoped_id(r)])
                        for c, r, _, _ in candidates
                        if scoped_id(r) in current
                    ]
                    data = (
                        await MemoryReadData.load(uow, context, records, policy)
                        if isinstance(self.sources, DatabaseSourceReader)
                        else None
                    )
                    for scoped, item, row_actions, visible_sources in candidates:
                        row = current.get(scoped_id(item))
                        if row is None:
                            continue
                        scoped_uow = UnitOfWork(uow.connection, scoped.scope, uow.keys)
                        row = await self.refresh(scoped_uow, scoped, row, data)
                        if (status and row["status"] != status) or (
                            not status and row["status"] == "REVOKED"
                        ):
                            continue
                        if data is not None and policy is not None:
                            visible_sources = data.visible.get(scoped_id(row), frozenset())
                        items.append(
                            await self.view(
                                scoped_uow, scoped, row, row_actions, visible_sources, data
                            )
                        )
                        if len(items) > limit:
                            break
            if len(rows) < limit + 1:
                break
        more = len(items) > limit
        selected = items[:limit]
        next_cursor = (
            encode_cursor(
                {
                    "binding": binding,
                    "ceiling": ceiling.isoformat(),
                    "time": selected[-1].created_at.isoformat(),
                    "id": selected[-1].memory_id,
                }
            )
            if more
            else None
        )
        async with transaction(self.engine, context.scope, repo.keys(context.scope)) as uow:
            attributes = await self.attributes(uow, context)
        return MemoryList(
            items=selected,
            next_cursor=next_cursor,
            has_more=more,
            attributes=attributes,
            actions=[a for a in actions if a.action_key in {"create", "policy"}],
        )
