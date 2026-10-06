"""记忆完整范围恢复、来源重算和版本协议。"""

from typing import Any

from sqlalchemy import delete, insert, select, update
from sqlalchemy.ext.asyncio import AsyncEngine

from creativity_service.core.context import AuthContext, Authorization, Scope
from creativity_service.core.contracts import VisibleAction
from creativity_service.core.database import Repository, UnitOfWork, scope_values, validate_row
from creativity_service.core.database.tables import metadata as core_metadata
from creativity_service.core.deletion import ContentRef, DeletionGuard, content_key
from creativity_service.core.primitives import ServiceError, digest, new_id, utcnow
from creativity_service.modules.conversations.tables import metadata as conversations
from creativity_service.modules.iam.reading import (
    read_actions,
    read_policy,
    resource_state,
    visible_actions,
)
from creativity_service.modules.memory import repositories as repo
from creativity_service.modules.memory.ports import MemorySourceReader, SourceState
from creativity_service.modules.memory.reading import MemoryReadData, scoped_id
from creativity_service.modules.memory.schemas import (
    ConsolidationSettings,
    MemoryAttribute,
    MemoryPolicy,
    MemorySourceView,
    PreferenceView,
    SourceInput,
)
from creativity_service.modules.memory.sources import DatabaseSourceReader
from creativity_service.modules.memory.tables import metadata


class MemoryKernel:
    def __init__(
        self,
        engine: AsyncEngine,
        authorization: Authorization,
        *,
        sources: MemorySourceReader | None = None,
    ) -> None:
        self.engine, self.authorization = engine, authorization
        self.sources = sources or DatabaseSourceReader()

    @staticmethod
    def require_subject(context: AuthContext) -> None:
        if not context.scope.subject_id:
            raise ServiceError("MEMORY_SUBJECT_REQUIRED", "请选择已授权的业务主体", 422)

    @staticmethod
    def row_context(context: AuthContext, row: dict[str, Any]) -> AuthContext:
        scope = Scope.model_validate({k: row[k] for k in Scope.model_fields})
        if scope == context.scope:
            return context
        if (
            context.principal_type == "management"
            and context.scope.subject_id is None
            and all(
                getattr(scope, k) == getattr(context.scope, k)
                for k in ("channel_id", "environment")
            )
        ):
            return context.model_copy(update={"scope": scope})
        raise ServiceError("NOT_FOUND", "记忆记录不存在", 404)

    async def locate(
        self, context: AuthContext, anchor_id: str, *, conversation: bool = False
    ) -> AuthContext:
        tables = [metadata.tables["memories"]]
        if conversation:
            tables.append(conversations.tables["conversations"])
        for table in tables:
            async with self.engine.connect() as connection:
                rows = (
                    (
                        await connection.execute(
                            select(table).where(
                                table.c.channel_id == context.scope.channel_id,
                                table.c.id == anchor_id,
                            )
                        )
                    )
                    .mappings()
                    .all()
                )
            if len(rows) > 1:
                raise ServiceError("STORAGE_INVARIANT_BROKEN", "主体引用重复", 503)
            if rows:
                scoped = self.row_context(context, dict(rows[0]))
                self.require_subject(scoped)
                return scoped
        raise ServiceError("NOT_FOUND", "主体引用不存在", 404)

    async def subject(self, context: AuthContext, anchor_id: str | None) -> AuthContext:
        if anchor_id:
            context = await self.locate(context, anchor_id, conversation=True)
        self.require_subject(context)
        return context

    async def allowed(self, context: AuthContext, action: str, resource_id: str = "scope") -> bool:
        try:
            await self.authorization.require(context, action, resource_id)
        except ServiceError as exc:
            if exc.status in {403, 404}:
                return False
            raise
        return True

    async def actions(
        self,
        context: AuthContext,
        resource_id: str = "scope",
        permissions: frozenset[str] | None = None,
    ) -> list[VisibleAction]:
        if permissions is None:
            permissions = await read_actions(
                self.authorization,
                context,
                "memory",
                resource_id,
                ["memory:write", "memory:delete"],
            )
        return visible_actions(
            permissions,
            [
                ("create", "新增记忆", "memory:write"),
                ("edit", "修正", "memory:write"),
                ("confirm", "确认", "memory:write"),
                ("delete", "遗忘", "memory:delete"),
            ],
        )

    async def preference_actions(
        self, context: AuthContext, permissions: frozenset[str] | None = None
    ) -> list[VisibleAction]:
        if permissions is None:
            permissions = await read_actions(
                self.authorization,
                context,
                "memory",
                "scope",
                ["memory:preferences", "memory:delete"],
            )
        return visible_actions(
            permissions,
            [
                ("preferences", "长期记忆", "memory:preferences"),
                ("clear", "清空记忆", "memory:delete"),
            ],
        )

    @staticmethod
    async def preference(
        uow: UnitOfWork, context: AuthContext, actions: list[VisibleAction]
    ) -> PreferenceView:
        row = await repo.one(uow.connection, "memory_preferences", context.scope)
        return PreferenceView(
            enabled=row["enabled"] if row else True,
            revision=row["revision"] if row else 0,
            actions=actions,
        )

    @staticmethod
    async def policy(
        uow: UnitOfWork, context: AuthContext, agent_id: str | None = None
    ) -> MemoryPolicy:
        row = await repo.one(uow.connection, "memory_policies", context.scope, agent_id=None)
        channel = (
            MemoryPolicy.model_validate({k: row[k] for k in MemoryPolicy.model_fields})
            if row
            else MemoryPolicy()
        )
        if agent_id is None:
            return channel
        row = await repo.one(uow.connection, "memory_policies", context.scope, agent_id=agent_id)
        if row is None:
            # 智能体未显式声明记忆能力时，不默认启用运行读取或建议写入。
            return channel.model_copy(
                update={"read_enabled": False, "suggest_enabled": False, "write_mode": "DISABLED"}
            )
        agent = MemoryPolicy.model_validate({k: row[k] for k in MemoryPolicy.model_fields})
        modes = ["DISABLED", "CANDIDATE", "EXPLICIT"]
        return MemoryPolicy(
            allowed_types=[t for t in agent.allowed_types if t in channel.allowed_types],
            read_enabled=channel.read_enabled and agent.read_enabled,
            suggest_enabled=channel.suggest_enabled and agent.suggest_enabled,
            write_mode=modes[min(modes.index(channel.write_mode), modes.index(agent.write_mode))],
            ttl_seconds=min(channel.ttl_seconds, agent.ttl_seconds),
            max_items=min(channel.max_items, agent.max_items),
            retrieval_limit=min(channel.retrieval_limit, agent.retrieval_limit),
            failure_mode=agent.failure_mode,
        )

    @staticmethod
    async def attributes(uow: UnitOfWork, context: AuthContext) -> list[MemoryAttribute]:
        from creativity_service.modules.memory.validation import ATTRIBUTES

        row = await repo.one(uow.connection, "memory_policies", context.scope, agent_id=None)
        values = row.get("attributes") if row else None
        return (
            [MemoryAttribute.model_validate(v) for v in values]
            if values is not None
            else list(ATTRIBUTES)
        )

    @staticmethod
    async def consolidation_settings(
        uow: UnitOfWork, context: AuthContext
    ) -> ConsolidationSettings:
        row = await repo.one(uow.connection, "memory_policies", context.scope, agent_id=None)
        return ConsolidationSettings.model_validate((row or {}).get("consolidation") or {})

    async def effective_policy(
        self, uow: UnitOfWork, context: AuthContext, agent_id: str, frozen: MemoryPolicy | None
    ) -> MemoryPolicy:
        # 发布快照本身即为 Agent 的显式声明；未单独保存覆盖策略时使用渠道上限。
        override = await repo.one(
            uow.connection, "memory_policies", context.scope, agent_id=agent_id
        )
        return await self.policy(uow, context, agent_id if override or frozen is None else None)

    async def version(
        self,
        uow: UnitOfWork,
        context: AuthContext,
        row: dict[str, Any],
        reason: str,
        **changes: Any,
    ) -> dict[str, Any]:
        previous_id = row.get("current_version_id")
        previous = (
            await repo.one(uow.connection, "memory_versions", context.scope, id=previous_id)
            if previous_id
            else None
        )
        if previous is None:
            previous_id = None
        version_id = new_id("memory_version")
        row = await repo.save(
            uow, "memories", row["id"], {**changes, "current_version_id": version_id}
        )
        sources = await repo.rows(
            uow.connection, "memory_sources", context.scope, memory_id=row["id"], status="ACTIVE"
        )
        await repo.save(
            uow,
            "memory_versions",
            version_id,
            {
                "memory_id": row["id"],
                "previous_version_id": previous_id,
                # 历史只存状态和依据引用，不保留可恢复的旧值副本。
                "value": None,
                "changed_by": context.principal_id,
                "reason": reason,
                "status": row["status"],
                "version_number": previous["version_number"] + 1 if previous else 1,
                "source_ids": [s["id"] for s in sources],
            },
        )
        return row

    async def source_state(
        self, uow: UnitOfWork, context: AuthContext, source: dict[str, Any]
    ) -> SourceState | None:
        try:
            await DeletionGuard(context.scope).check(
                uow, [ContentRef(source["source_type"], source["source_id"])]
            )
            if source["source_type"] == "memory_input":
                return SourceState(
                    "人工修正" if source["authority"] == "ADMIN" else "用户明确输入",
                    source["authority"],
                    source["trust_level"],
                    source["observed_at"],
                )
            return await self.sources.resolve(
                uow,
                context,
                SourceInput(
                    source_type=source["source_type"],
                    source_id=source["source_id"],
                    source_version=source["source_version"],
                ),
            )
        except ServiceError as exc:
            if exc.code == "CONTENT_DELETED":
                return None
            raise

    async def link(
        self, uow: UnitOfWork, context: AuthContext, source: dict[str, Any], memory_id: str
    ) -> None:
        uow.require_lock(content_key(context.scope))
        table = core_metadata.tables["source_links"]
        link_id = digest(
            [
                context.scope.model_dump(),
                "memory",
                memory_id,
                source["source_type"],
                source["source_id"],
            ]
        )
        if await Repository(table, context.scope).get(uow.connection, link_id):
            return
        row = {
            **scope_values(table, context.scope),
            "id": link_id,
            "created_at": utcnow(),
            "updated_at": utcnow(),
            "revision": 1,
            "source_type": source["source_type"],
            "source_id": source["source_id"],
            "derived_type": "memory",
            "derived_id": memory_id,
            "source_version": source["source_version"],
        }
        validate_row(table, row)
        await uow.connection.execute(insert(table).values(**row))

    @staticmethod
    async def scrub_versions(uow: UnitOfWork, context: AuthContext, memory_id: str) -> None:
        table = metadata.tables["memory_versions"]
        await uow.connection.execute(
            update(table)
            .where(
                Repository(table, context.scope).predicate(),
                table.c.memory_id == memory_id,
            )
            .values(value=None, source_ids=[])
        )

    async def refresh(
        self,
        uow: UnitOfWork,
        context: AuthContext,
        row: dict[str, Any],
        data: MemoryReadData | None = None,
    ) -> dict[str, Any]:
        """先剔除失效来源再检查派生内容，独立来源采用任一有效依据语义。"""
        sources = (
            data.sources.get(scoped_id(row), [])
            if data is not None
            else await repo.rows(
                uow.connection,
                "memory_sources",
                context.scope,
                memory_id=row["id"],
                status="ACTIVE",
            )
        )
        valid, changed = [], False
        for source in sources:
            state = (
                data.states.get(scoped_id(source))
                if data is not None
                else await self.source_state(uow, context, source)
            )
            if state and (row["memory_type"] != "FACT" or row["key"] in state.fact_keys):
                valid.append(source)
                continue
            changed = True
            await repo.save(
                uow, "memory_sources", source["id"], {"status": "REVOKED", "evidence_id": None}
            )
            links = core_metadata.tables["source_links"]
            await uow.connection.execute(
                delete(links).where(
                    Repository(links, context.scope).predicate(),
                    links.c.derived_type == "memory",
                    links.c.derived_id == row["id"],
                    links.c.source_type == source["source_type"],
                    links.c.source_id == source["source_id"],
                )
            )
        marked = changed and row.get("source_mode") == "ALL"
        try:
            if data is None or changed:
                await DeletionGuard(context.scope).check(uow, [ContentRef("memory", row["id"])])
            elif ContentRef("memory", row["id"]) in data.blocked:
                marked = True
        except ServiceError as exc:
            if exc.code != "CONTENT_DELETED":
                raise
            marked = True
        if marked or not valid:
            # 撤销前固化记忆自身标记；来源边移除后，旧文件和快照仍不能读取旧值。
            markers = core_metadata.tables["deletion_markers"]
            marker_id = digest([context.scope.model_dump(), "memory", row["id"]])
            if not await Repository(markers, context.scope).get(uow.connection, marker_id):
                marker = {
                    **scope_values(markers, context.scope),
                    "id": marker_id,
                    "created_at": utcnow(),
                    "updated_at": utcnow(),
                    "revision": 1,
                    "target_type": "memory",
                    "target_id": row["id"],
                    "reason_code": "MEMORY_SOURCE_REVOKED",
                    "requested_by": context.principal_id,
                }
                validate_row(markers, marker)
                await uow.connection.execute(insert(markers).values(**marker))
            if (
                row["status"] != "REVOKED"
                or row["value"] is not None
                or row["subject_name"] is not None
            ):
                row = await self.version(
                    uow,
                    context,
                    row,
                    "SOURCE_REVOKED",
                    status="REVOKED",
                    value=None,
                    subject_name=None,
                )
            await self.scrub_versions(uow, context, row["id"])
        elif changed:
            await self.scrub_versions(uow, context, row["id"])
            row = await self.version(
                uow,
                context,
                row,
                "SOURCE_CHANGED",
                trust_level=max(s["trust_level"] for s in valid),
                observed_at=max(s["observed_at"] for s in valid),
                subject_name=None,
            )
        if row["status"] in {"ACTIVE", "PROPOSED"} and row["expires_at"] <= utcnow():
            row = await self.version(uow, context, row, "EXPIRED", status="EXPIRED")
        return row

    async def source_views(
        self,
        uow: UnitOfWork,
        context: AuthContext,
        row: dict[str, Any],
        visible_sources: frozenset[str] = frozenset(),
        data: MemoryReadData | None = None,
    ) -> list[MemorySourceView]:
        if row["status"] == "REVOKED":
            return []
        result = []
        sources = (
            data.sources.get(scoped_id(row), [])
            if data is not None
            else await repo.rows(
                uow.connection,
                "memory_sources",
                context.scope,
                memory_id=row["id"],
                status="ACTIVE",
            )
        )
        for source in sources:
            state = (
                data.states.get(scoped_id(source))
                if data is not None
                else await self.source_state(uow, context, source)
            )
            if state:
                result.append(
                    MemorySourceView(
                        name=state.name
                        if source["source_type"] == "memory_input"
                        or source["source_id"] in visible_sources
                        else "来源名称不可用",
                        source_type_label={
                            "USER": "用户输入",
                            "ADMIN": "人工修正",
                            "TOOL": "业务工具",
                            "ARCHIVE": "会话归档",
                        }[state.authority],
                        source_version=source["source_version"],
                        observed_at=state.observed_at,
                    )
                )
        return result

    async def visible_sources(
        self, context: AuthContext, memory_id: str | None = None
    ) -> frozenset[str]:
        """批量读取标题所需关系，在事务外按唯一父资源计算显示权限。"""
        from creativity_service.modules.tools.tables import metadata as tools

        policy = await read_policy(self.authorization, context)
        async with self.engine.connect() as connection:
            sources = await repo.rows(
                connection,
                "memory_sources",
                context.scope,
                **({"memory_id": memory_id} if memory_id else {}),
                status="ACTIVE",
            )
            messages = await Repository(conversations.tables["messages"], context.scope).get_many(
                connection, [s["source_id"] for s in sources if s["source_type"] == "message"]
            )
            evidence = await Repository(tools.tables["evidence_refs"], context.scope).get_many(
                connection, [s["source_id"] for s in sources if s["source_type"] == "evidence"]
            )
            calls = await Repository(tools.tables["tool_calls"], context.scope).get_many(
                connection,
                [r["source_id"] for r in evidence.values() if r["source_type"] == "tool_call"],
            )
            parents = {
                "memory": await Repository(metadata.tables["memories"], context.scope).get_many(
                    connection, [s["source_id"] for s in sources if s["source_type"] == "memory"]
                ),
                "conversation": await Repository(
                    conversations.tables["conversations"], context.scope
                ).get_many(connection, [m["conversation_id"] for m in messages.values()]),
                "tool": await Repository(tools.tables["tools"], context.scope).get_many(
                    connection, [c["tool_id"] for c in calls.values()]
                ),
            }
        result = set()
        permissions: dict[tuple[str, str], frozenset[str]] = {}
        for source in sources:
            kind, identifier, action = "", "", ""
            if source["source_type"] == "message" and (
                message := messages.get(source["source_id"])
            ):
                kind, identifier, action = (
                    "conversation",
                    message["conversation_id"],
                    "conversation:read",
                )
            elif source["source_type"] == "memory":
                kind, identifier, action = "memory", source["source_id"], "memory:read"
            elif source["source_type"] == "evidence" and (ref := evidence.get(source["source_id"])):
                call = calls.get(ref["source_id"]) if ref["source_type"] == "tool_call" else None
                if call:
                    kind, identifier, action = "tool", call["tool_id"], "tool:manage"
            parent = parents.get(kind, {}).get(identifier)
            if parent is None:
                continue
            key = (kind, identifier)
            if key not in permissions:
                permissions[key] = await read_actions(
                    self.authorization,
                    context,
                    kind,
                    identifier,
                    [action],
                    policy=policy,
                    state=resource_state(context, kind, parent),
                )
            if action in permissions[key]:
                result.add(source["source_id"])
        return frozenset(result)
