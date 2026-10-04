"""智能体服务装配及管理资源读取；执行器和评测器由后续单元显式注入。"""

from sqlalchemy.ext.asyncio import AsyncEngine

from creativity_service.core.auth.types import ResourceState, ResourceStateReader
from creativity_service.core.context import AuthContext
from creativity_service.core.database import transaction
from creativity_service.core.deletion import CleanupRegistry, ContentRef, DeletionGuard
from creativity_service.core.primitives import ServiceError
from creativity_service.modules.agents.access import locked_require
from creativity_service.modules.agents.repositories import repository
from creativity_service.modules.agents.services import AgentService
from creativity_service.modules.budgets.services import BudgetService
from creativity_service.modules.iam.authorization import IamAuthorization
from creativity_service.modules.releases.ports import AgentDebugRunner, EvaluationGate
from creativity_service.modules.skills.services import SkillService
from creativity_service.modules.tools.services import ToolService


class AgentResourceReader:
    display_tables = {"agent": ["agents"], "version": ["resource_versions"]}

    def __init__(self, engine: AsyncEngine, fallback: ResourceStateReader | None) -> None:
        self.engine, self.fallback = engine, fallback

    async def read_current(
        self, context: AuthContext, resource_type: str, resource_id: str
    ) -> ResourceState | None:
        if resource_type in {"agent", "version"}:
            async with self.engine.connect() as connection:
                row = await repository(
                    "agents" if resource_type == "agent" else "resource_versions", context.scope
                ).get(connection, resource_id)
            if row and (resource_type == "agent" or row["resource_type"] == "agent"):
                return ResourceState(
                    scope=context.scope,
                    resource_type=resource_type,
                    resource_id=resource_id,
                    name=row.get("name") or row["version_label"],
                    active=True,
                )
        return (
            await self.fallback.read_current(context, resource_type, resource_id)
            if self.fallback
            else None
        )


def build_agent_service(
    engine: AsyncEngine,
    authorization: IamAuthorization,
    tools: ToolService,
    skills: SkillService,
    budgets: BudgetService,
    *,
    evaluation: EvaluationGate | None = None,
    runner: AgentDebugRunner | None = None,
    cleanup: CleanupRegistry | None = None,
) -> AgentService:
    authorization.resources = AgentResourceReader(engine, authorization.resources)
    service = AgentService(engine, authorization, tools, skills, budgets, evaluation, runner)
    if cleanup is not None:

        async def clear(context: AuthContext, ref: ContentRef) -> None:
            async with engine.connect() as connection:
                row = await repository("agent_candidates", context.scope).get(
                    connection, ref.resource_id
                )
            if row is None:
                raise ServiceError("NOT_FOUND", "候选快照不存在", 404)
            await service.require(context, "content:cleanup", row["agent_id"])
            async with transaction(
                engine,
                context.scope,
                service.keys(context, row["agent_id"], ("agent_candidates", ref.resource_id)),
            ) as uow:
                await locked_require(uow, context, "content:cleanup", "agent", row["agent_id"])
                try:
                    await DeletionGuard(context.scope).check(uow, [ref])
                except ServiceError as exc:
                    if exc.code != "CONTENT_DELETED":
                        raise
                else:
                    raise ServiceError("DELETION_MARKER_REQUIRED", "清理前必须登记删除标记")
                current = await repository("agent_candidates", context.scope).get(
                    uow.connection, ref.resource_id
                )
                if current:
                    await repository("agent_candidates", context.scope).change(
                        uow, ref.resource_id, current["revision"], {"spec": {}}
                    )

        cleanup.register("agent_candidate", clear)
    return service
