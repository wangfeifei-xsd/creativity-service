"""一页记忆的来源、版本和显示权限批量读取，保留每条记录的完整主体范围。"""

from collections import defaultdict
from typing import Any

from sqlalchemy import Table, and_, or_, select
from sqlalchemy.ext.asyncio import AsyncConnection

from creativity_service.core.context import AuthContext, Scope
from creativity_service.core.database import Repository, UnitOfWork
from creativity_service.core.deletion import ContentRef, DeletionGuard
from creativity_service.core.primitives import ServiceError, utcnow
from creativity_service.modules.conversations.tables import metadata as conversations
from creativity_service.modules.iam.authorization import ReadAuthorization
from creativity_service.modules.iam.reading import resource_state
from creativity_service.modules.memory.ports import SourceState
from creativity_service.modules.memory.tables import metadata
from creativity_service.modules.tools.tables import metadata as tools


def scoped_id(row: dict[str, Any], identifier: str | None = None) -> tuple[Any, ...]:
    return (*(row.get(k) for k in Scope.model_fields), identifier or row["id"])


async def scoped_rows(
    connection: AsyncConnection,
    table: Table,
    requests: list[tuple[AuthContext, str]],
    *,
    field: str = "id",
    **filters: Any,
) -> list[dict[str, Any]]:
    predicates = {}
    for context, identifier in requests:
        predicates[(*context.scope.model_dump().values(), identifier)] = and_(
            Repository(table, context.scope).predicate(), table.c[field] == identifier
        )
    result: list[dict[str, Any]] = []
    values = list(predicates.values())
    for start in range(0, len(values), 200):
        statement = select(table).where(
            or_(*values[start : start + 200]),
            *(table.c[k] == value for k, value in filters.items()),
        )
        result.extend(dict(r) for r in (await connection.execute(statement)).mappings())
    return result


def indexed(rows: list[dict[str, Any]]) -> dict[tuple[Any, ...], dict[str, Any]]:
    result = {scoped_id(row): row for row in rows}
    if len(result) != len(rows):
        raise ServiceError("STORAGE_INVARIANT_BROKEN", "记录标识重复，请核查", 503)
    return result


class MemoryReadData:
    def __init__(self) -> None:
        self.sources: dict[tuple[Any, ...], list[dict[str, Any]]] = defaultdict(list)
        self.states: dict[tuple[Any, ...], SourceState | None] = {}
        self.visible: dict[tuple[Any, ...], frozenset[str]] = {}
        self.versions: dict[tuple[Any, ...], dict[str, Any]] = {}
        self.blocked: frozenset[ContentRef] = frozenset()

    @classmethod
    async def load(
        cls,
        uow: UnitOfWork,
        context: AuthContext,
        records: list[tuple[AuthContext, dict[str, Any]]],
        policy: ReadAuthorization | None,
    ) -> "MemoryReadData":
        data = cls()
        source_rows = await scoped_rows(
            uow.connection,
            metadata.tables["memory_sources"],
            [(c, r["id"]) for c, r in records],
            field="memory_id",
            status="ACTIVE",
        )
        contexts = {tuple(c.scope.model_dump().values()): c for c, _ in records}
        requests = [(contexts[scoped_id(s)[:-1]], s) for s in source_rows]
        related = {}
        for kind, table in (
            ("memory", metadata.tables["memories"]),
            ("message", conversations.tables["messages"]),
            ("evidence", tools.tables["evidence_refs"]),
        ):
            related[kind] = indexed(
                await scoped_rows(
                    uow.connection,
                    table,
                    [(c, s["source_id"]) for c, s in requests if s["source_type"] == kind],
                )
            )
        parents = indexed(
            await scoped_rows(
                uow.connection,
                conversations.tables["conversations"],
                [
                    (contexts[scoped_id(r)[:-1]], r["conversation_id"])
                    for r in related["message"].values()
                ],
            )
        )
        calls: list[dict[str, Any]] = []
        evidence = list(related["evidence"].values())
        call_table = tools.tables["tool_calls"]
        # 只读本批证据的支撑调用，不能扫描该主体全部历史工具调用。
        for start in range(0, len(evidence), 200):
            predicates = [
                and_(
                    Repository(call_table, contexts[scoped_id(e)[:-1]].scope).predicate(),
                    call_table.c.evidence_ids.contains([e["id"]]),
                )
                for e in evidence[start : start + 200]
            ]
            calls.extend(
                dict(r)
                for r in (
                    await uow.connection.execute(
                        select(call_table).where(
                            call_table.c.state == "SUCCEEDED", or_(*predicates)
                        )
                    )
                ).mappings()
            )
        calls = list({scoped_id(c): c for c in calls}.values())
        tool_rows = await scoped_rows(
            uow.connection,
            tools.tables["tools"],
            [(contexts[scoped_id(c)[:-1]], c["tool_id"]) for c in calls],
        )
        tool_map = {r["id"]: r for r in tool_rows}
        refs = [ContentRef("memory", r["id"]) for _, r in records]
        refs.extend(ContentRef(s["source_type"], s["source_id"]) for _, s in requests)
        refs.extend(ContentRef("tool_call", r["id"]) for r in calls)
        refs.extend(ContentRef(e["source_type"], e["source_id"]) for e in evidence)
        data.blocked = await DeletionGuard(context.scope).blocked_refs(
            uow, refs, scopes=[c.scope for c, _ in records]
        )
        data.versions = indexed(
            await scoped_rows(
                uow.connection,
                metadata.tables["memory_versions"],
                [(c, r["current_version_id"]) for c, r in records],
            )
        )
        visible: dict[tuple[Any, ...], set[str]] = defaultdict(set)
        for scoped, source in requests:
            key = scoped_id(source)
            memory_key = scoped_id(source, source["memory_id"])
            data.sources[memory_key].append(source)
            state = None
            action, kind, parent = "", "", None
            source_ref = ContentRef(source["source_type"], source["source_id"])
            if source_ref not in data.blocked:
                row = related.get(source["source_type"], {}).get(
                    scoped_id(source, source["source_id"])
                )
                if source["source_type"] == "memory_input":
                    state = SourceState(
                        "人工修正" if source["authority"] == "ADMIN" else "用户明确输入",
                        source["authority"],
                        source["trust_level"],
                        source["observed_at"],
                    )
                elif (
                    source["source_type"] == "memory"
                    and row
                    and row["memory_type"] == "ARCHIVE"
                    and row["status"] == "ACTIVE"
                    and row["expires_at"] > utcnow()
                    and row["current_version_id"] == source["source_version"]
                ):
                    state = SourceState("会话归档", "ARCHIVE", 1, row["observed_at"])
                    action, kind, parent = "memory:read", "memory", row
                elif (
                    source["source_type"] == "message"
                    and row
                    and row["role"] in {"user", "assistant"}
                    and str(row["sequence"]) == source["source_version"]
                ):
                    parent = parents.get(scoped_id(row, row["conversation_id"]))
                    if (
                        parent
                        and parent["status"] not in {"DELETING", "DELETED"}
                        and parent["expires_at"] > utcnow()
                    ):
                        state = SourceState(
                            parent["title"],
                            "USER" if row["role"] == "user" else "ARCHIVE",
                            4 if row["role"] == "user" else 1,
                            row["created_at"],
                        )
                        action, kind = "conversation:read", "conversation"
                elif (
                    source["source_type"] == "evidence"
                    and row
                    and row["source_version"] == source["source_version"]
                ):
                    supporting = [
                        c
                        for c in calls
                        if scoped_id(c)[:-1] == scoped_id(row)[:-1]
                        and row["id"] in c["evidence_ids"]
                        and ContentRef("tool_call", c["id"]) not in data.blocked
                    ]
                    if (
                        supporting
                        and ContentRef(row["source_type"], row["source_id"]) not in data.blocked
                    ):
                        path = row["location"].get("field_path") or []
                        state = SourceState(
                            row["title"] or "业务工具",
                            "TOOL",
                            3,
                            row["observed_at"],
                            frozenset([str(path[-1])]) if path else frozenset(),
                        )
                        original = next(
                            (
                                c
                                for c in calls
                                if c["id"] == row["source_id"]
                                and scoped_id(c)[:-1] == scoped_id(row)[:-1]
                            ),
                            None,
                        )
                        if row["source_type"] == "tool_call" and original:
                            action, kind, parent = (
                                "tool:manage",
                                "tool",
                                tool_map.get(original["tool_id"]),
                            )
            data.states[key] = state
            if state and action and parent and policy is not None:
                if action in policy.actions(
                    kind, parent["id"], resource_state(scoped, kind, parent), context=scoped
                ):
                    visible[memory_key].add(source["source_id"])
        data.visible = {key: frozenset(value) for key, value in visible.items()}
        return data
