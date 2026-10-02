"""智能体服务装配及管理资源读取；执行器和评测器由后续单元显式注入。"""

from sqlalchemy.ext.asyncio import AsyncEngine

from creativity_service.core.auth.types import ResourceState, ResourceStateReader
from creativity_service.core.context import AuthContext
from creativity_service.modules.agents.repositories import repository
from creativity_service.modules.agents.services import AgentService
from creativity_service.modules.budgets.services import BudgetService
from creativity_service.modules.iam.authorization import IamAuthorization
from creativity_service.modules.releases.ports import AgentDebugRunner, EvaluationGate
from creativity_service.modules.skills.services import SkillService
from creativity_service.modules.tools.services import ToolService


class AgentResourceReader:
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
) -> AgentService:
    authorization.resources = AgentResourceReader(engine, authorization.resources)
    return AgentService(engine, authorization, tools, skills, budgets, evaluation, runner)
