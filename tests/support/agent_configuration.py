"""底层运行与会话测试的内部配置夹具，不经过智能体管理接口或真实模型。"""

from creativity_service.core.database import transaction
from creativity_service.core.deletion import ContentRef, DeletionGuard
from creativity_service.core.locking import record_key
from creativity_service.core.primitives import digest
from creativity_service.core.versioning import version_view
from creativity_service.modules.agents.repositories import repository
from creativity_service.modules.resources.configuration import configuration_values


async def save_configuration(service, context, agent_id, content, dependencies, output_schema):
    source_id = digest([agent_id, "agent", agent_id])
    keys = service.keys(context.scope, agent_id, "fixture")
    keys.append(record_key(context.scope.channel_id, "source_links", source_id))
    async with transaction(service.engine, context.scope, keys) as uow:
        repo = repository("resource_versions", context.scope)
        values = configuration_values(
            "agent", agent_id, content, dependencies, output_schema, context.principal_id
        )
        values["state"] = "CURRENT"
        current = await repo.get(uow.connection, agent_id)
        if current:
            row = await repo.change(uow, agent_id, current["revision"], values)
        else:
            row = await repo.add(uow, agent_id, values)
            await DeletionGuard(context.scope).link(
                uow, source_id, ContentRef("agent", agent_id), ContentRef("version", agent_id)
            )
    return version_view(row)
