"""会话入口只列出当前环境已发布、获执行授权并启用会话的智能体。"""

from creativity_service.core.context import AuthContext
from creativity_service.core.database import transaction
from creativity_service.core.deletion import ContentRef, DeletionGuard, content_key
from creativity_service.modules.agents.repositories import repository
from creativity_service.modules.agents.schemas import AgentDefinition
from creativity_service.modules.agents.services import AgentService
from creativity_service.modules.conversations.schemas import AgentChoice
from creativity_service.modules.iam.reading import resource_state


class ConversationAgents:
    def __init__(self, agents: AgentService) -> None:
        self.agents = agents

    async def list_available(self, context: AuthContext) -> list[AgentChoice]:
        policy = await self.agents.authorization.read_policy(context)
        choices = []
        async with transaction(
            self.agents.engine, context.scope, [content_key(context.scope)]
        ) as uow:
            agents = await repository("agents", context.scope).find(uow.connection, status="ACTIVE")
            agents = [
                row
                for row in agents
                if "run:create"
                in policy.actions("agent", row["id"], resource_state(context, "agent", row))
            ]
            identifiers = [self.agents.mapping_id(context, row["id"]) for row in agents]
            states = await repository("agent_environment_states", context.scope).get_many(
                uow.connection, identifiers
            )
            mappings = await repository("release_mappings", context.scope).get_many(
                uow.connection, identifiers
            )
            versions = await repository("resource_versions", context.scope).get_many(
                uow.connection, [r["version_id"] for r in mappings.values()]
            )
            blocked = await DeletionGuard(context.scope).blocked_refs(
                uow,
                [
                    *(ContentRef("agent", row["id"]) for row in agents),
                    *(ContentRef("version", identifier) for identifier in versions),
                ],
            )
            for agent in agents:
                identifier = self.agents.mapping_id(context, agent["id"])
                state, mapping = states.get(identifier), mappings.get(identifier)
                version = versions.get(mapping["version_id"]) if mapping else None
                if (
                    not version
                    or version["state"] != "PUBLISHED"
                    or (state and state["status"] != "ACTIVE")
                ):
                    continue
                if {
                    ContentRef("agent", agent["id"]),
                    ContentRef("version", version["id"]),
                } & blocked:
                    continue
                if AgentDefinition.model_validate(version["content"]).context.conversation_enabled:
                    choices.append(AgentChoice(agent_code=agent["agent_code"], name=agent["name"]))
        return sorted(choices, key=lambda choice: choice.name)
