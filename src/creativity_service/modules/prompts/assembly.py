"""提示词生产装配与内容清理登记，不装配模拟运行或测试证据。"""

from sqlalchemy.ext.asyncio import AsyncEngine

from creativity_service.core.artifacts import ArtifactService, ObjectStore
from creativity_service.core.context import AuthContext, Authorization
from creativity_service.core.database import transaction
from creativity_service.core.deletion import CleanupRegistry, ContentRef, DeletionGuard, content_key
from creativity_service.core.locking import record_key
from creativity_service.core.primitives import ServiceError
from creativity_service.modules.iam.authorization import IamAuthorization
from creativity_service.modules.prompts.authorization import (
    PromptAuthorization,
    PromptResourceReader,
)
from creativity_service.modules.prompts.repositories import repository
from creativity_service.modules.prompts.services import PromptService


def build_prompt_service(
    engine: AsyncEngine, authorization: IamAuthorization, store: ObjectStore
) -> PromptService:
    authorization.resources = PromptResourceReader(engine, authorization.resources)
    return PromptService(
        engine,
        authorization,
        ArtifactService(engine, store, PromptAuthorization(engine, authorization)),
    )


def register_prompt_cleanup(
    registry: CleanupRegistry, engine: AsyncEngine, authorization: Authorization
) -> None:
    async def clean(context: AuthContext, ref: ContentRef) -> None:
        await authorization.require(context, "content:cleanup", "scope")
        table = {"prompt_sample": "prompt_samples", "prompt_test": "prompt_tests"}[
            ref.resource_type
        ]
        async with transaction(
            engine,
            context.scope,
            [
                content_key(context.scope),
                record_key(context.scope.channel_id, table, ref.resource_id),
            ],
        ) as uow:
            try:
                await DeletionGuard(context.scope).check(uow, [ref])
            except ServiceError as exc:
                if exc.code != "CONTENT_DELETED":
                    raise
            else:
                raise ServiceError("DELETION_MARKER_REQUIRED", "清理前必须登记删除标记")
            row = await repository(table, context.scope).get(uow.connection, ref.resource_id)
            if row:
                values = (
                    {"input": {}, "expected_constraints": [], "title": "已删除样例"}
                    if table == "prompt_samples"
                    else {
                        "frozen_version": {},
                        "sample_snapshot": {},
                        "rendered_input": {},
                        "is_deleted": True,
                        "status": "DELETED",
                    }
                )
                await repository(table, context.scope).change(
                    uow, ref.resource_id, row["revision"], values
                )

    registry.register("prompt_sample", clean)
    registry.register("prompt_test", clean)
