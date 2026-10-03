"""渠道授权上限与智能体显式声明；运行始终应用最新渠道收紧策略。"""

from creativity_service.core.context import AuthContext
from creativity_service.core.contracts import VisibleAction
from creativity_service.core.database import Repository, transaction
from creativity_service.core.database.tables import metadata
from creativity_service.core.primitives import ServiceError, digest
from creativity_service.modules.memory import repositories as repo
from creativity_service.modules.memory.base import MemoryKernel
from creativity_service.modules.memory.schemas import MemoryPolicy, PolicyInput, PolicyView
from creativity_service.modules.memory.validation import validate_attributes, validate_policy


class MemoryPolicies(MemoryKernel):
    async def validate_agent_policy(self, context: AuthContext, policy: MemoryPolicy) -> None:
        await self.authorization.require(context, "agent:manage", "scope")
        async with transaction(self.engine, context.scope, repo.keys(context.scope)) as uow:
            validate_policy(policy, await self.policy(uow, context))

    async def get_policy(self, context: AuthContext, agent_id: str | None = None) -> PolicyView:
        await self.authorization.require(context, "channel:manage", context.scope.channel_id)
        async with transaction(self.engine, context.scope, repo.keys(context.scope)) as uow:
            policy = await self.policy(uow, context, agent_id)
            row = await repo.one(
                uow.connection, "memory_policies", context.scope, agent_id=agent_id
            )
            attributes = await self.attributes(uow, context)
            consolidation = await self.consolidation_settings(uow, context)
        return PolicyView(
            **policy.model_dump(),
            attributes=attributes,
            consolidation=consolidation,
            revision=row["revision"] if row else 0,
            actions=[VisibleAction(action_key="policy", label="保存策略")],
        )

    async def set_policy(
        self, context: AuthContext, body: PolicyInput, agent_id: str | None = None
    ) -> PolicyView:
        await self.authorization.require(context, "channel:manage", context.scope.channel_id)
        if agent_id and (body.attributes is not None or body.consolidation is not None):
            raise ServiceError("MEMORY_POLICY_INVALID", "画像属性与整理时间须在渠道策略配置", 422)
        policy = MemoryPolicy.model_validate(
            body.model_dump(exclude={"revision", "attributes", "consolidation"})
        )
        async with transaction(self.engine, context.scope, repo.keys(context.scope)) as uow:
            if agent_id:
                versions = await Repository(
                    metadata.tables["resource_versions"], context.scope
                ).find(uow.connection, resource_type="agent", resource_id=agent_id)
                if not versions:
                    raise ServiceError("NOT_FOUND", "当前渠道没有此智能体", 404)
            validate_policy(policy, await self.policy(uow, context) if agent_id else None)
            row = await repo.one(
                uow.connection, "memory_policies", context.scope, agent_id=agent_id
            )
            if (row["revision"] if row else 0) != body.revision:
                raise ServiceError("REVISION_CONFLICT", "记忆策略已变化，请刷新后重试", 409)
            attributes = (
                body.attributes
                if body.attributes is not None
                else await self.attributes(uow, context)
            )
            consolidation = body.consolidation or await self.consolidation_settings(uow, context)
            validate_attributes(attributes)
            row = await repo.save(
                uow,
                "memory_policies",
                row["id"] if row else digest([context.scope.channel_id, "memory-policy", agent_id]),
                {
                    "agent_id": agent_id,
                    **policy.model_dump(),
                    "attributes": [a.model_dump() for a in attributes],
                    "consolidation": consolidation.model_dump(),
                },
            )
        return PolicyView(
            **policy.model_dump(),
            attributes=attributes,
            consolidation=consolidation,
            revision=row["revision"],
            actions=[VisibleAction(action_key="policy", label="保存策略")],
        )
