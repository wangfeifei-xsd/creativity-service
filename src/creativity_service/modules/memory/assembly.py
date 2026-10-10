"""记忆权限读取、运行端口及来源清理处理器的生产装配。"""

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncEngine

from creativity_service.core.auth.types import ResourceState, ResourceStateReader
from creativity_service.core.context import AuthContext, Scope
from creativity_service.core.database.soft_delete import active_rows
from creativity_service.core.deletion import CleanupRegistry
from creativity_service.core.primitives import ServiceError
from creativity_service.modules.iam.authorization import IamAuthorization
from creativity_service.modules.memory.services import MemoryService
from creativity_service.modules.memory.tables import metadata


class MemoryReader:
    display_tables = {"memory": ["memories"]}

    def __init__(self, engine: AsyncEngine, fallback: ResourceStateReader | None) -> None:
        self.engine, self.fallback = engine, fallback

    async def read_current(
        self, context: AuthContext, resource_type: str, resource_id: str
    ) -> ResourceState | None:
        if resource_type != "memory":
            return (
                await self.fallback.read_current(context, resource_type, resource_id)
                if self.fallback
                else None
            )
        table = metadata.tables["memories"]
        async with self.engine.connect() as connection:
            row = (
                (
                    await connection.execute(
                        active_rows(
                            select(table).where(
                                table.c.channel_id == context.scope.channel_id,
                                table.c.id == resource_id,
                            )
                        )
                    )
                )
                .mappings()
                .one_or_none()
            )
        return (
            ResourceState(
                scope=Scope.model_validate({k: row[k] for k in Scope.model_fields}),
                resource_type="memory",
                resource_id=resource_id,
                name=row["display_name"],
                active=True,
            )
            if row
            else None
        )


class MemoryAuthorization:
    def __init__(self, authorization: IamAuthorization) -> None:
        self.authorization = authorization

    async def require(self, context: AuthContext, action: str, resource_id: str) -> None:
        if action == "content:cleanup":
            if not (await self.authorization.check(context, action, "memory", resource_id)).allowed:
                raise ServiceError("FORBIDDEN", "无权清理此记忆", 403)
        else:
            await self.authorization.require(context, action, resource_id)


def build_memory_service(
    engine: AsyncEngine, authorization: IamAuthorization, cleanup: CleanupRegistry | None = None
) -> MemoryService:
    authorization.resources = MemoryReader(engine, authorization.resources)
    service = MemoryService(engine, MemoryAuthorization(authorization))
    if cleanup is not None:
        cleanup.register("memory", service.clean)
    return service
