"""公共内容清理装配，业务模块须登记自己的处理器。"""

from sqlalchemy.ext.asyncio import AsyncEngine

from creativity_service.core.artifacts import ArtifactService
from creativity_service.core.context import AuthContext, Authorization
from creativity_service.core.database import Repository, transaction
from creativity_service.core.database.tables import metadata
from creativity_service.core.deletion import CleanupRegistry, ContentRef, DeletionGuard, content_key
from creativity_service.core.locking import record_key
from creativity_service.core.primitives import ServiceError

CORE_CONTENT_TYPES = {
    table.info["cleanup_type"] for table in metadata.tables.values() if table.info.get("content")
}


def register_core_handlers(
    registry: CleanupRegistry,
    engine: AsyncEngine,
    artifacts: ArtifactService,
    authorization: Authorization,
) -> None:
    async def clear_content(context: AuthContext, ref: ContentRef) -> None:
        await authorization.require(context, "content:cleanup", ref.resource_id)
        table_name = {"version": "resource_versions", "snapshot": "release_snapshots"}[
            ref.resource_type
        ]
        repo = Repository(metadata.tables[table_name], context.scope)
        async with transaction(
            engine,
            context.scope,
            [
                content_key(context.scope),
                record_key(context.scope.channel_id, table_name, ref.resource_id),
            ],
        ) as uow:
            try:
                await DeletionGuard(context.scope).check(uow, [ref])
            except ServiceError as exc:
                if exc.code != "CONTENT_DELETED":
                    raise
            else:
                raise ServiceError("DELETION_MARKER_REQUIRED", "清理前必须登记删除标记")
            row = await repo.get(uow.connection, ref.resource_id)
            if row:
                values = (
                    {
                        "content": {},
                        "output_schema": {},
                        "state": "RETIRED",
                        "version_label": "已删除版本",
                    }
                    if ref.resource_type == "version"
                    else {"versions": [], "output_schema": {}}
                )
                await repo.change(uow, row["id"], row["revision"], values)

    registry.register("artifact", artifacts.clean)
    registry.register("version", clear_content)
    registry.register("snapshot", clear_content)
    registry.validate(CORE_CONTENT_TYPES)
