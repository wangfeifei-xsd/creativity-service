"""运行服务与 IAM 资源查询装配；正式解析器及执行器由方案 17 注入。"""

from sqlalchemy import delete
from sqlalchemy.ext.asyncio import AsyncEngine

from creativity_service.core.auth.types import ResourceState, ResourceStateReader
from creativity_service.core.context import AuthContext, Scope
from creativity_service.core.database import transaction
from creativity_service.core.deletion import CleanupRegistry, ContentRef, DeletionGuard
from creativity_service.core.primitives import ServiceError
from creativity_service.core.versioning import VersionService
from creativity_service.modules.budgets.services import BudgetService
from creativity_service.modules.iam.services import IamServices
from creativity_service.modules.runs.ports import DefinitionResolver, TurnHooks
from creativity_service.modules.runs.repositories import one
from creativity_service.modules.runs.services import RunService
from creativity_service.modules.runs.tables import metadata
from creativity_service.modules.usage.services import UsageService


class RunResourceReader:
    def __init__(self, engine: AsyncEngine, fallback: ResourceStateReader | None) -> None:
        self.engine, self.fallback = engine, fallback

    async def read_current(
        self, context: AuthContext, resource_type: str, resource_id: str
    ) -> ResourceState | None:
        if resource_type not in {"run", "content"}:
            return (
                await self.fallback.read_current(context, resource_type, resource_id)
                if self.fallback
                else None
            )
        async with self.engine.connect() as connection:
            row = await one(connection, "runs", context.scope.channel_id, id=resource_id)
        if row is None and resource_type == "content":
            return (
                await self.fallback.read_current(context, resource_type, resource_id)
                if self.fallback
                else None
            )
        if row is None or (context.client_id and row["client_id"] != context.client_id):
            return None
        return ResourceState(
            scope=Scope.model_validate({k: row[k] for k in Scope.model_fields}),
            resource_type=resource_type,
            resource_id=resource_id,
            name=row["agent_name"],
            active=True,
        )


def build_run_service(
    engine: AsyncEngine,
    iam: IamServices,
    versions: VersionService,
    budgets: BudgetService,
    ledger: UsageService,
    *,
    resolver: DefinitionResolver | None = None,
    turns: TurnHooks | None = None,
    cleanup: CleanupRegistry | None = None,
) -> RunService:
    iam.authorization.resources = RunResourceReader(engine, iam.authorization.resources)
    runs = RunService(
        engine, iam.authorization, versions, budgets, ledger, resolver=resolver, turns=turns
    )
    if cleanup is not None:

        async def clear(context: AuthContext, ref: ContentRef) -> None:
            if not (
                await iam.authorization.check(context, "content:cleanup", "run", ref.resource_id)
            ).allowed:
                raise ServiceError("FORBIDDEN", "无权清理此运行内容", 403)
            async with transaction(
                engine, context.scope, runs.keys(context, ref.resource_id)
            ) as uow:
                row = await runs.locked_run(uow, ref.resource_id)
                try:
                    await DeletionGuard(context.scope).check(uow, [ref])
                except ServiceError as exc:
                    if exc.code != "CONTENT_DELETED":
                        raise
                else:
                    raise ServiceError("DELETION_MARKER_REQUIRED", "清理前必须登记删除标记")
                # 删除传播只清理原文与恢复内容，费用、状态、尝试和审计证据继续保留。
                for name in ("run_contents", "checkpoints"):
                    table = metadata.tables[name]
                    await uow.connection.execute(
                        delete(table).where(
                            table.c.channel_id == context.scope.channel_id,
                            table.c.run_id == row["id"],
                        )
                    )

        cleanup.register("run", clear)
    return runs


def require_runs(value: object) -> RunService:
    if not isinstance(value, RunService):
        raise ServiceError("DEPENDENCY_UNAVAILABLE", "运行服务暂不可用", 503)
    return value
