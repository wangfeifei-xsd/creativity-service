"""仅为首次完成当前授权复核、且没有任何历史内容的主体建立访问屏障。"""

from sqlalchemy.ext.asyncio import AsyncEngine

from creativity_service.core.context import AuthContext
from creativity_service.core.database import Repository, transaction
from creativity_service.core.database.tables import metadata
from creativity_service.core.deletion import RecoveryService, barrier_id
from creativity_service.core.primitives import ServiceError


async def initialize_subject_content(engine: AsyncEngine, context: AuthContext) -> None:
    scope = context.scope
    repo = Repository(metadata.tables["recovery_barriers"], scope)
    async with engine.connect() as connection:
        if await repo.get(connection, barrier_id(scope)) is not None:
            return
    parent = scope.model_copy(update={"subject_type": None, "subject_id": None})
    async with transaction(engine, scope, RecoveryService.keys(scope)) as uow:
        if await repo.get(uow.connection, barrier_id(scope)) is not None:
            return
        domain = await Repository(metadata.tables["recovery_barriers"], parent).get(
            uow.connection, barrier_id(parent)
        )
        if not domain or domain["state"] != "READY":
            raise ServiceError("RECOVERY_BLOCKED", "环境内容恢复核对尚未完成", 503)
        from creativity_service.storage import metadata as all_metadata

        # 有历史任务、证据或内容时，缺失屏障只能走恢复核对，不能被重新委托洗成新主体。
        for table in all_metadata.tables.values():
            if "subject_id" in table.c and await Repository(table, scope).find(uow.connection):
                raise ServiceError("RECOVERY_PROOF_REQUIRED", "主体已有内容，须先完成恢复核对", 503)
        await RecoveryService.initialize_fresh_in(uow, scope)
