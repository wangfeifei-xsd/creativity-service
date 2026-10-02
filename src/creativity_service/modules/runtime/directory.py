"""会话入口只列出当前环境已发布、获执行授权并启用会话的智能体。"""

from creativity_service.core.context import AuthContext
from creativity_service.core.database import transaction
from creativity_service.core.deletion import ContentRef, DeletionGuard
from creativity_service.core.primitives import ServiceError
from creativity_service.modules.agents.access import locked_require
from creativity_service.modules.agents.repositories import repository
from creativity_service.modules.agents.schemas import AgentDefinition
from creativity_service.modules.agents.services import AgentService
from creativity_service.modules.conversations.schemas import AgentChoice


class ConversationAgents:
    def __init__(self, agents: AgentService) -> None:
        self.agents = agents

    async def list_available(self, context: AuthContext) -> list[AgentChoice]:
        await self.agents.authorization.authentication.revalidate(context)
        async with self.agents.engine.connect() as connection:
            agents = await repository("agents", context.scope).find(connection, status="ACTIVE")
        choices = []
        for agent in agents:
            try:
                await self.agents.require(context, "run:create", agent["id"])
                async with transaction(
                    self.agents.engine, context.scope, self.agents.keys(context, agent["id"])
                ) as uow:
                    await locked_require(uow, context, "run:create", "agent", agent["id"])
                    mapping_id = self.agents.mapping_id(context, agent["id"])
                    state = await repository("agent_environment_states", context.scope).get(
                        uow.connection, mapping_id
                    )
                    mapping = await repository("release_mappings", context.scope).get(
                        uow.connection, mapping_id
                    )
                    if not mapping or (state and state["status"] != "ACTIVE"):
                        continue
                    version = await repository("resource_versions", context.scope).get(
                        uow.connection, mapping["version_id"]
                    )
                    if not version or version["state"] != "PUBLISHED":
                        continue
                    await DeletionGuard(context.scope).check(
                        uow,
                        [ContentRef("agent", agent["id"]), ContentRef("version", version["id"])],
                    )
                    if AgentDefinition.model_validate(
                        version["content"]
                    ).context.conversation_enabled:
                        choices.append(
                            AgentChoice(agent_code=agent["agent_code"], name=agent["name"])
                        )
            except ServiceError as exc:
                if exc.status not in {403, 404, 410}:
                    raise
        return sorted(choices, key=lambda choice: choice.name)
