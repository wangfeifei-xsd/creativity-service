"""工具生产装配与 IAM 资源读取组合，不提供放行运行限额的默认实现。"""

from dataclasses import dataclass
from typing import Any

from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncEngine

from creativity_service.core.auth.types import ResourceState, ResourceStateReader
from creativity_service.core.context import AuthContext
from creativity_service.core.database import Repository, transaction
from creativity_service.core.deletion import CleanupRegistry, ContentRef, DeletionGuard, content_key
from creativity_service.core.locking import record_key
from creativity_service.core.primitives import ServiceError
from creativity_service.integrations.tools import AdapterRegistry
from creativity_service.integrations.tools.builtin import register_builtins
from creativity_service.modules.iam.authorization import IamAuthorization
from creativity_service.modules.tools.execution import RedisToolCache, ToolExecutor
from creativity_service.modules.tools.ports import ToolRunPort
from creativity_service.modules.tools.services import ToolService
from creativity_service.modules.tools.tables import metadata


class ToolResourceReader:
    display_tables = {"tool": ["tools"]}

    def __init__(self, engine: AsyncEngine, fallback: ResourceStateReader | None) -> None:
        self.engine, self.fallback = engine, fallback

    async def read_current(
        self, context: AuthContext, resource_type: str, resource_id: str
    ) -> ResourceState | None:
        if resource_type != "tool":
            return (
                await self.fallback.read_current(context, resource_type, resource_id)
                if self.fallback
                else None
            )
        async with self.engine.connect() as connection:
            row = await Repository(metadata.tables["tools"], context.scope).get(
                connection, resource_id
            )
        # 停用资源仍可查看配置和影响，执行层单独阻断其调用。
        return (
            ResourceState(
                scope=context.scope,
                resource_type="tool",
                resource_id=resource_id,
                name=row["name"],
                active=True,
            )
            if row
            else None
        )


@dataclass(frozen=True)
class ToolServices:
    management: ToolService
    executor: ToolExecutor
    registry: AdapterRegistry


def build_tool_services(
    engine: AsyncEngine,
    authorization: IamAuthorization,
    *,
    redis: Redis | None = None,
    prefix: str = "creativity",
    registry: AdapterRegistry | None = None,
    runs: ToolRunPort | None = None,
) -> ToolServices:
    if registry is None:
        registry = AdapterRegistry()
        register_builtins(registry)
    authorization.resources = ToolResourceReader(engine, authorization.resources)
    service = ToolService(engine, authorization, registry, runs)
    return ToolServices(
        service,
        ToolExecutor(service, runs, RedisToolCache(redis, prefix, engine) if redis else None),
        registry,
    )


def register_tool_cleanup(
    registry: CleanupRegistry, engine: AsyncEngine, authorization: IamAuthorization
) -> None:
    async def clean(context: AuthContext, ref: ContentRef) -> None:
        await authorization.require(context, "content:cleanup", "scope")
        table_name = {"tool_call": "tool_calls", "evidence": "evidence_refs"}[ref.resource_type]
        scope = context.scope
        async with transaction(
            engine,
            scope,
            [content_key(scope), record_key(scope.channel_id, table_name, ref.resource_id)],
        ) as uow:
            try:
                await DeletionGuard(scope).check(uow, [ref])
            except ServiceError as exc:
                if exc.code != "CONTENT_DELETED":
                    raise
            else:
                raise ServiceError("DELETION_MARKER_REQUIRED", "清理前必须登记删除标记", 409)
            repo = Repository(metadata.tables[table_name], scope)
            row = await repo.get(uow.connection, ref.resource_id)
            if row:
                values: dict[str, Any] = (
                    {
                        "redacted_arguments": {},
                        "result_summary": None,
                        "error": None,
                        "attempt": None,
                        "evidence_ids": [],
                        "result_ref": None,
                    }
                    if ref.resource_type == "tool_call"
                    else {
                        "location": {},
                        "title": None,
                        "artifact_id": None,
                        "authorization_scope": {},
                    }
                )
                await repo.change(uow, ref.resource_id, row["revision"], values)

    registry.register("tool_call", clean)
    registry.register("evidence", clean)
