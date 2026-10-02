"""技能服务装配及文件、测试内容的删除清理登记。"""

from sqlalchemy.ext.asyncio import AsyncEngine

from creativity_service.core.artifacts import ObjectStore
from creativity_service.core.context import AuthContext
from creativity_service.core.database import transaction
from creativity_service.core.deletion import CleanupRegistry, ContentRef, DeletionGuard, content_key
from creativity_service.core.locking import record_key
from creativity_service.core.primitives import ServiceError
from creativity_service.modules.iam.authorization import IamAuthorization
from creativity_service.modules.skills.authorization import SkillResourceReader
from creativity_service.modules.skills.repositories import repository
from creativity_service.modules.skills.services import SkillService
from creativity_service.modules.tools.services import ToolService


def build_skill_service(
    engine: AsyncEngine, authorization: IamAuthorization, store: ObjectStore, tools: ToolService
) -> SkillService:
    authorization.resources = SkillResourceReader(engine, authorization.resources)
    return SkillService(engine, authorization, store, tools)


def register_skill_cleanup(registry: CleanupRegistry, service: SkillService) -> None:
    async def clean(context: AuthContext, ref: ContentRef) -> None:
        await service.authorization.require(context, "content:cleanup", "scope")
        table = {"skill_file": "skill_files", "skill_test": "skill_tests"}[ref.resource_type]
        async with transaction(
            service.engine,
            context.scope,
            [
                content_key(context.scope),
                record_key(context.scope.channel_id, table, ref.resource_id),
            ],
        ) as uow:
            repo = repository(table, context.scope)
            row = await repo.get(uow.connection, ref.resource_id)
            if row is None:
                return
            try:
                await DeletionGuard(context.scope).check(
                    uow, [ref, ContentRef("version", row["version_id"])]
                )
            except ServiceError as exc:
                if exc.code != "CONTENT_DELETED":
                    raise
            else:
                raise ServiceError("DELETION_MARKER_REQUIRED", "清理前必须登记删除标记", 409)
            if table == "skill_files":
                await repo.remove(uow, ref.resource_id)
            else:
                await repo.change(
                    uow,
                    ref.resource_id,
                    row["revision"],
                    {
                        "context_snapshot": {},
                        "selected_files": [],
                        "result": {},
                        "run_id": None,
                        "release_snapshot_id": None,
                    },
                )

    registry.register("skill_file", clean)
    registry.register("skill_test", clean)
