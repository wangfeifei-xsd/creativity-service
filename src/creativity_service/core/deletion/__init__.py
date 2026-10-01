"""删除标记、来源图和恢复屏障；最终清理调度由方案 25 接入。"""

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Protocol

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncEngine

from creativity_service.core.context import AuthContext, Authorization, DenyAuthorization, Scope
from creativity_service.core.database import (
    Repository,
    UnitOfWork,
    assert_external_io_allowed,
    transaction,
)
from creativity_service.core.database.tables import metadata
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
        uow.require_scope(self.scope)
        uow.require_lock(content_key(self.scope))
        barrier = await self.barriers.get(uow.connection, barrier_id(self.scope))
        if barrier is None or barrier["state"] != "READY":
            raise ServiceError("RECOVERY_BLOCKED", "内容恢复核对尚未完成", 503)
        markers = await self.markers.find(uow.connection)
        if any(m["target_type"] == "scope" for m in markers):
            raise ServiceError("CONTENT_DELETED", "该范围内容已删除", 410)
        deleted = {ContentRef(m["target_type"], m["target_id"]) for m in markers}
        visited: set[ContentRef] = set()
        pending = list(refs)
        while pending:
            ref = pending.pop()
            if ref in visited:
                continue
            if ref.resource_type in CONFIG_CONTENT_TYPES:
                table = metadata.tables["deletion_markers"]
                marked = await uow.connection.scalar(
                    select(table.c.id)
                    .where(
                        table.c.channel_id == self.scope.channel_id,
                        table.c.target_type == ref.resource_type,
                        table.c.target_id == ref.resource_id,
                    )
                    .limit(1)
                )
                if marked is not None:
                    raise ServiceError("CONTENT_DELETED", "配置内容或来源已删除", 410)
            if ref in deleted:
                raise ServiceError("CONTENT_DELETED", "内容或来源已删除", 410)
            visited.add(ref)
            if len(visited) > 10000:
                raise ServiceError("SOURCE_GRAPH_LIMIT", "来源关系过多，需核对后读取", 503)
            links = await self.sources.find(
                uow.connection, derived_type=ref.resource_type, derived_id=ref.resource_id
            )
            if ref.resource_type in CONFIG_CONTENT_TYPES:
                table = metadata.tables["source_links"]
                links = [
                    dict(row)
                    for row in (
                        await uow.connection.execute(
                            select(table).where(
                                table.c.channel_id == self.scope.channel_id,
                                table.c.derived_type == ref.resource_type,
                                table.c.derived_id == ref.resource_id,
                            )
                        )
                    ).mappings()
                ]
            pending.extend(ContentRef(link["source_type"], link["source_id"]) for link in links)

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
        repo = Repository(metadata.tables["recovery_barriers"], scope)
        async with transaction(self.engine, scope, self.keys(scope)) as uow:
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
