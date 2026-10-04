"""删除标记、来源图和恢复屏障的唯一公共检查协议。"""

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Protocol

from sqlalchemy import select, tuple_
from sqlalchemy.ext.asyncio import AsyncEngine

from creativity_service.core.context import AuthContext, Authorization, DenyAuthorization, Scope
from creativity_service.core.database import (
    Repository,
    UnitOfWork,
    assert_external_io_allowed,
    transaction,
)
from creativity_service.core.database.tables import metadata
from creativity_service.core.deletion.ledger import (
    DeletionLedger,
    current_manifest,
    maintenance_mode,
)
from creativity_service.core.deletion.resources import CONTENT_MODELS
from creativity_service.core.locking import ResourceKey, record_key
from creativity_service.core.primitives import ServiceError, digest, new_id, unavailable, utcnow


@dataclass(frozen=True)
class ContentRef:
    resource_type: str
    resource_id: str

    def __post_init__(self) -> None:
        if not self.resource_type or not self.resource_id or self.resource_type == "scope":
            raise ValueError("内容引用须指定实际对象类型及标识")


def content_key(scope: Scope) -> ResourceKey:
    # 配置版本跨环境共享；渠道内的短内容事务共用图锁，避免来源删除与派生写入竞争。
    return ResourceKey(scope.channel_id, "content-graph", ("channel",))


def barrier_id(scope: Scope) -> str:
    return digest(["recovery", scope.model_dump()])


CONFIG_CONTENT_TYPES = {
    "version",
    "agent",
    "prompt",
    "tool",
    "skill",
    "model",
    "model_connection",
    "model_route",
    "budget_policy",
    "risk_policy",
    "metric",
}


class DeletionGuard:
    def __init__(self, scope: Scope) -> None:
        self.scope = scope
        self.markers = Repository(metadata.tables["deletion_markers"], scope)
        self.sources = Repository(metadata.tables["source_links"], scope)
        self.barriers = Repository(metadata.tables["recovery_barriers"], scope)

    async def check(self, uow: UnitOfWork, refs: list[ContentRef]) -> None:
        if await self.blocked_refs(uow, refs):
            raise ServiceError("CONTENT_DELETED", "内容或来源已删除", 410)

    async def blocked_refs(
        self, uow: UnitOfWork, refs: list[ContentRef], *, scopes: list[Scope] | None = None
    ) -> frozenset[ContentRef]:
        """在同一图锁下按层批量读取来源，返回每个输入对象的删除结果。"""
        uow.require_scope(self.scope)
        uow.require_lock(content_key(self.scope))
        targets = {
            barrier_id(scope): scope for scope in (scopes if scopes is not None else [self.scope])
        }
        if any(scope.channel_id != self.scope.channel_id for scope in targets.values()):
            raise ServiceError("SCOPE_MISMATCH", "删除检查不能跨渠道", 403)
        table = metadata.tables["recovery_barriers"]
        barriers = {}
        identifiers = list(targets)
        for start in range(0, len(identifiers), 500):
            records = await uow.connection.execute(
                select(table).where(
                    table.c.channel_id == self.scope.channel_id,
                    table.c.id.in_(identifiers[start : start + 500]),
                )
            )
            for record in records.mappings():
                if record["id"] in barriers:
                    raise ServiceError("STORAGE_INVARIANT_BROKEN", "恢复屏障标识重复", 503)
                barriers[record["id"]] = record
        manifest = current_manifest.get() or {}
        if not maintenance_mode.get() and (
            any(
                identifier not in barriers or barriers[identifier]["state"] != "READY"
                for identifier in targets
            )
            or manifest.get("blocked")
        ):
            raise ServiceError("RECOVERY_BLOCKED", "内容恢复核对尚未完成", 503)
        table = metadata.tables["deletion_markers"]
        markers = [
            dict(row)
            for row in (
                await uow.connection.execute(
                    select(table).where(table.c.channel_id == self.scope.channel_id)
                )
            ).mappings()
        ]
        markers.extend(manifest.get("entries", []))
        deleted_scopes = {
            tuple((m.get("scope") or m).get(k) for k in Scope.model_fields)
            for m in markers
            if m["target_type"] == "scope"
        }
        if scopes is None and tuple(self.scope.model_dump().values()) in deleted_scopes:
            raise ServiceError("CONTENT_DELETED", "该范围内容已删除", 410)
        deleted = {
            ContentRef(m["target_type"], m["target_id"])
            for m in markers
            if m["target_type"] != "scope"
        }
        # 已核对数据库与独立清单，没有删除记录时无需查询任何来源边。
        if not deleted and not deleted_scopes:
            return frozenset()
        visited: set[ContentRef] = set()
        pending = set(refs)
        blocked: set[ContentRef] = set()
        parents: dict[ContentRef, set[ContentRef]] = {}

        def connect(parent: ContentRef, source: ContentRef) -> None:
            parents.setdefault(source, set()).add(parent)
            if source not in visited:
                pending.add(source)

        while pending:
            batch = list(pending - visited)[:500]
            pending.difference_update(batch)
            visited.update(batch)
            blocked.update(set(batch) & deleted)
            if len(visited) > 10000:
                raise ServiceError("SOURCE_GRAPH_LIMIT", "来源关系过多，需核对后读取", 503)
            batch = [ref for ref in batch if ref not in blocked]
            if not batch:
                continue
            # 同渠道来源可跨主体；读取来源自己的归属，不能套用调用方的主体。
            kinds = {ref.resource_type for ref in batch}
            if deleted_scopes:
                for kind in kinds:
                    model = CONTENT_MODELS.get(kind)
                    if model is None or "environment" not in model.c:
                        continue
                    records = await uow.connection.execute(
                        select(model).where(
                            model.c.channel_id == self.scope.channel_id,
                            model.c.id.in_(
                                [r.resource_id for r in batch if r.resource_type == kind]
                            ),
                        )
                    )
                    blocked.update(
                        ContentRef(kind, row["id"])
                        for row in records.mappings()
                        if tuple(row.get(k) for k in Scope.model_fields) in deleted_scopes
                    )
            table = metadata.tables["source_links"]
            links = [
                dict(row)
                for row in (
                    await uow.connection.execute(
                        select(table).where(
                            table.c.channel_id == self.scope.channel_id,
                            tuple_(table.c.derived_type, table.c.derived_id).in_(
                                [(r.resource_type, r.resource_id) for r in batch]
                            ),
                        )
                    )
                ).mappings()
            ]
            for link in links:
                parent = ContentRef(link["derived_type"], link["derived_id"])
                if tuple(link.get(k) for k in Scope.model_fields) in deleted_scopes:
                    blocked.add(parent)
                connect(parent, ContentRef(link["source_type"], link["source_id"]))
            run_ids = [r.resource_id for r in batch if r.resource_type == "run"]
            if run_ids:
                # 受理后的输入及工具内容先于上下文来源边出现，旧运行也须立即阻断。
                for kind in ("message", "tool_call"):
                    model = CONTENT_MODELS.get(kind)
                    if model is not None:
                        records = await uow.connection.execute(
                            select(model.c.id, model.c.run_id).where(
                                model.c.channel_id == self.scope.channel_id,
                                model.c.run_id.in_(run_ids),
                            )
                        )
                        for row in records.mappings():
                            connect(ContentRef("run", row["run_id"]), ContentRef(kind, row["id"]))
        affected = list(blocked)
        while affected:
            for parent in parents.get(affected.pop(), set()):
                if parent not in blocked:
                    blocked.add(parent)
                    affected.append(parent)
        return frozenset(set(refs) & blocked)

    async def link(
        self,
        uow: UnitOfWork,
        link_id: str,
        source: ContentRef,
        derived: ContentRef,
        source_version: str | None = None,
    ) -> None:
        await self.check(uow, [source, derived])
        if (
            derived.resource_type in CONFIG_CONTENT_TYPES
            and source.resource_type not in CONFIG_CONTENT_TYPES
        ):
            raise ServiceError("CONFIG_SOURCE_INVALID", "配置不能继承个人内容作为隐式来源", 422)
        if source == derived:
            raise ServiceError("SOURCE_CYCLE", "内容不能引用自身", 422)
        await self.sources.add(
            uow,
            link_id,
            {
                "source_type": source.resource_type,
                "source_id": source.resource_id,
                "derived_type": derived.resource_type,
                "derived_id": derived.resource_id,
                "source_version": source_version,
            },
        )


class DeletionService:
    def __init__(self, engine: AsyncEngine, authorization: Authorization | None = None) -> None:
        self.engine = engine
        self.authorization = authorization or DenyAuthorization()

    async def mark(self, context: AuthContext, target: ContentRef | None, reason_code: str) -> str:
        assert_external_io_allowed()
        await self.authorization.require(
            context, "content:delete", target.resource_id if target else "scope"
        )
        scope = context.scope
        await DeletionLedger().record(
            scope,
            target.resource_type if target else "scope",
            target.resource_id if target else "all",
            reason_code,
            context.principal_id,
        )
        marker_id = digest(
            [
                scope.model_dump(),
                target.resource_type if target else "scope",
                target.resource_id if target else "all",
            ]
        )
        repository = Repository(metadata.tables["deletion_markers"], scope)
        async with transaction(
            self.engine,
            scope,
            [content_key(scope), record_key(scope.channel_id, "deletion_markers", marker_id)],
        ) as uow:
            if await repository.get(uow.connection, marker_id) is None:
                await repository.add(
                    uow,
                    marker_id,
                    {
                        "target_type": target.resource_type if target else "scope",
                        "target_id": target.resource_id if target else "all",
                        "reason_code": reason_code,
                        "requested_by": context.principal_id,
                    },
                )
        return marker_id


@dataclass(frozen=True)
class RecoveryProof:
    scope: Scope
    recovery_id: str
    marker_digest: str


class RecoveryVerifier(Protocol):
    async def reconcile(self, scope: Scope, recovery_id: str) -> RecoveryProof: ...


class RecoveryService:
    def __init__(self, engine: AsyncEngine, authorization: Authorization | None = None) -> None:
        self.engine = engine
        self.authorization = authorization or DenyAuthorization()

    @staticmethod
    def keys(scope: Scope) -> list[ResourceKey]:
        return [
            content_key(scope),
            record_key(scope.channel_id, "recovery_barriers", barrier_id(scope)),
        ]

    @staticmethod
    async def marker_digest(uow: UnitOfWork, scope: Scope) -> str:
        rows = await Repository(metadata.tables["deletion_markers"], scope).find(uow.connection)
        return digest(sorted(r["id"] for r in rows))

    async def initialize_fresh(self, context: AuthContext) -> None:
        """仅为无历史内容的新范围开屏障，恢复已有数据必须提供独立删除账本证明。"""
        assert_external_io_allowed()
        await self.authorization.require(context, "recovery:initialize", "scope")
        scope = context.scope
        async with transaction(self.engine, scope, self.keys(scope)) as uow:
            await self.initialize_fresh_in(uow, scope)

    @staticmethod
    async def initialize_fresh_in(uow: UnitOfWork, scope: Scope) -> None:
        """仅供已核准的新数据域创建事务复用；已有屏障或历史内容始终拒绝。"""
        uow.require_scope(scope)
        uow.require_lock(content_key(scope))
        repo = Repository(metadata.tables["recovery_barriers"], scope)
        if await repo.get(uow.connection, barrier_id(scope)) is not None:
            raise ServiceError("RECOVERY_EXISTS", "恢复范围已经初始化")
        for table_name in (
            "artifacts",
            "source_links",
            "deletion_markers",
            "release_snapshots",
        ):
            if await Repository(metadata.tables[table_name], scope).find(uow.connection):
                raise ServiceError("RECOVERY_PROOF_REQUIRED", "已有数据须核对外部删除账本", 503)
        await repo.add(
            uow,
            barrier_id(scope),
            {
                "state": "READY",
                "recovery_id": new_id("fresh"),
                "marker_digest": digest([]),
                "verified_at": utcnow(),
            },
        )

    async def block(self, context: AuthContext) -> str:
        assert_external_io_allowed()
        await self.authorization.require(context, "recovery:block", "scope")
        scope, recovery_id = context.scope, new_id("recovery")
        repo = Repository(metadata.tables["recovery_barriers"], scope)
        async with transaction(self.engine, scope, self.keys(scope)) as uow:
            row = await repo.get(uow.connection, barrier_id(scope))
            values = {
                "state": "BLOCKED",
                "recovery_id": recovery_id,
                "marker_digest": await self.marker_digest(uow, scope),
                "verified_at": None,
            }
            if row is None:
                await repo.add(uow, barrier_id(scope), values)
            else:
                await repo.change(uow, row["id"], row["revision"], values)
        return recovery_id

    async def complete(
        self, context: AuthContext, recovery_id: str, verifier: RecoveryVerifier | None
    ) -> None:
        assert_external_io_allowed()
        await self.authorization.require(context, "recovery:complete", "scope")
        if verifier is None:
            raise unavailable("独立删除账本核对服务")
        proof = await verifier.reconcile(context.scope, recovery_id)
        scope = context.scope
        repo = Repository(metadata.tables["recovery_barriers"], scope)
        async with transaction(self.engine, scope, self.keys(scope)) as uow:
            row = await repo.get(uow.connection, barrier_id(scope))
            if (
                row is None
                or row["state"] != "BLOCKED"
                or row["recovery_id"] != recovery_id
                or proof.scope != scope
                or proof.recovery_id != recovery_id
                or proof.marker_digest != await self.marker_digest(uow, scope)
            ):
                raise ServiceError("RECOVERY_PROOF_INVALID", "恢复证明与当前删除账本不一致", 503)
            await repo.change(
                uow,
                row["id"],
                row["revision"],
                {"state": "READY", "marker_digest": proof.marker_digest, "verified_at": utcnow()},
            )


CleanupHandler = Callable[[AuthContext, ContentRef], Awaitable[None]]


class CleanupRegistry:
    def __init__(self) -> None:
        self.handlers: dict[str, CleanupHandler] = {}

    def register(self, resource_type: str, handler: CleanupHandler) -> None:
        if resource_type in self.handlers:
            raise ValueError("清理处理器重复登记")
        self.handlers[resource_type] = handler

    def validate(self, required_types: set[str]) -> None:
        missing = required_types - self.handlers.keys()
        if missing:
            raise RuntimeError(f"缺少内容清理处理器：{', '.join(sorted(missing))}")

    async def clean(self, context: AuthContext, ref: ContentRef) -> None:
        handler = self.handlers.get(ref.resource_type)
        if handler is None:
            raise unavailable("内容清理处理器")
        await handler(context, ref)
