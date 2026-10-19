"""智能体上下线与启停用独立控制，操作在内容锁内复核配置和授权。"""

from creativity_service.core.context import AuthContext
from creativity_service.core.database import transaction
from creativity_service.core.observability.audit import append_audit
from creativity_service.core.primitives import new_id
from creativity_service.modules.agents.access import locked_require
from creativity_service.modules.agents.repositories import repository, required
from creativity_service.modules.agents.schemas import AgentDefinition, AgentDetail, AgentStateInput
from creativity_service.modules.agents.services import AgentService


class AgentLifecycle:
    def __init__(self, agents: AgentService) -> None:
        self.agents = agents

    async def state(
        self, context: AuthContext, agent_id: str, body: AgentStateInput
    ) -> AgentDetail:
        s, scope = self.agents, context.scope
        await s.require(context, "release:publish", agent_id)
        _, configuration = await s.raw(context, agent_id)
        identifier, audit_id = s.mapping_id(context, agent_id), new_id("audit")
        keys = s.keys(
            context,
            agent_id,
            ("resource_versions", agent_id),
            ("agent_environment_states", identifier),
            ("audit_events", audit_id),
        )
        if body.operation in {"online", "enable"}:
            keys += await s.dependency_keys(
                context, AgentDefinition.model_validate(configuration["content"])
            )
        async with transaction(s.engine, scope, keys) as uow:
            await locked_require(uow, context, "release:publish", "agent", agent_id)
            agent = await required(uow.connection, scope, "agents", agent_id)
            repo = repository("agent_environment_states", scope)
            previous = await repo.get(uow.connection, identifier)
            online = bool(previous and previous["is_online"])
            enabled = not previous or previous["status"] == "ACTIVE"
            if body.operation in {"online", "offline"}:
                online = body.operation == "online"
            else:
                enabled = body.operation == "enable"
            if online and body.operation in {"online", "enable"}:
                _, current = await s.locked_version(
                    uow, context, agent_id, agent_id, configuration["revision"], "release:publish"
                )
                check, _, _ = await s.inspect_in(
                    uow, context, agent, current, "production", require_evaluation=False
                )
                s.require_valid(check)
            await repository("agents", scope).change(uow, agent_id, body.revision, {})
            values = {
                "agent_id": agent_id,
                "is_online": online,
                "status": "ACTIVE" if enabled else "DISABLED",
                "reason": body.reason,
            }
            if previous:
                await repo.change(uow, identifier, previous["revision"], values)
            else:
                await repo.add(uow, identifier, values)
            await append_audit(
                uow,
                context,
                audit_id,
                f"agent.{body.operation}",
                "agent",
                agent_id,
                {"online": online, "enabled": enabled},
            )
        return await s.detail(context, agent_id)
