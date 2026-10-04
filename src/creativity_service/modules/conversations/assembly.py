"""会话权限读取、产物服务和运行钩子的生产装配。"""

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncEngine

from creativity_service.core.artifacts import ArtifactService, ObjectStore
from creativity_service.core.auth.types import ResourceState, ResourceStateReader
from creativity_service.core.context import AuthContext, Authorization, Scope
from creativity_service.core.database import Repository, UnitOfWork
from creativity_service.core.database.tables import metadata as core_metadata
from creativity_service.core.deletion import CleanupRegistry
from creativity_service.core.locking import ResourceKey
from creativity_service.core.primitives import ServiceError
from creativity_service.modules.channels import repositories as channels
from creativity_service.modules.channels.schemas import RetentionPolicy
from creativity_service.modules.conversations.services import ConversationService
from creativity_service.modules.conversations.tables import metadata
from creativity_service.modules.iam.authorization import IamAuthorization
from creativity_service.modules.iam.repositories import policy_key
from creativity_service.modules.runs.services import RunService


class ConversationReader:
    display_tables = {"conversation": ["conversations"], "artifact": ["artifacts"]}

    def __init__(self, engine: AsyncEngine, fallback: ResourceStateReader | None) -> None:
        self.engine, self.fallback = engine, fallback

    async def read_current(
        self, context: AuthContext, resource_type: str, resource_id: str
    ) -> ResourceState | None:
        if resource_type == "artifact":
            async with self.engine.connect() as connection:
                artifact = await Repository(core_metadata.tables["artifacts"], context.scope).get(
                    connection, resource_id
                )
            if artifact:
                return ResourceState(
                    scope=context.scope,
                    resource_type="artifact",
                    resource_id=resource_id,
                    name=artifact["name"],
                    active=artifact["state"] in {"STAGED", "AVAILABLE"},
                )
        if resource_type == "conversation":
            table = metadata.tables["conversations"]
            async with self.engine.connect() as connection:
                row = (
                    (
                        await connection.execute(
                            select(table).where(
                                table.c.channel_id == context.scope.channel_id,
                                table.c.id == resource_id,
                            )
                        )
                    )
                    .mappings()
                    .one_or_none()
                )
            if row:
                return ResourceState(
                    scope=Scope.model_validate({k: row[k] for k in Scope.model_fields}),
                    resource_type=resource_type,
                    resource_id=resource_id,
                    name=row["title"],
                    active=True,
                )
            return None
        return (
            await self.fallback.read_current(context, resource_type, resource_id)
            if self.fallback
            else None
        )


class ConversationAuthorization:
    def __init__(self, authorization: Authorization) -> None:
        self.authorization = authorization

    async def require(self, context: AuthContext, action: str, resource_id: str) -> None:
        if isinstance(self.authorization, IamAuthorization) and action.split(":")[0] in {
            "conversation",
            "content",
            "data",
        }:
            decision = await self.authorization.check(context, action, "conversation", resource_id)
            if not decision.allowed:
                raise ServiceError("FORBIDDEN", "无权执行此会话操作", 403)
        else:
            await self.authorization.require(context, action, resource_id)


class ChannelRetention:
    def keys(self, context: AuthContext) -> list[ResourceKey]:
        return [policy_key(context.scope.channel_id)]

    async def days(self, uow: UnitOfWork, context: AuthContext) -> int:
        uow.require_scope(context.scope)
        uow.require_lock(policy_key(context.scope.channel_id))
        channel = await channels.required(
            uow.connection, "channels", context.scope.channel_id, id=context.scope.channel_id
        )
        environment = await channels.required(
            uow.connection,
            "channel_environments",
            context.scope.channel_id,
            environment=context.scope.environment,
        )
        if channel["status"] != "ACTIVE" or environment["status"] != "ACTIVE":
            raise ServiceError("CHANNEL_UNAVAILABLE", "渠道或环境不可用", 403)
        return min(
            RetentionPolicy.model_validate(row["retention_policy"]).retention_days
            for row in (channel, environment)
        )


def build_conversation_service(
    engine: AsyncEngine,
    authorization: IamAuthorization,
    runs: RunService,
    store: ObjectStore,
    cleanup: CleanupRegistry | None = None,
) -> ConversationService:
    authorization.resources = ConversationReader(engine, authorization.resources)
    scoped = ConversationAuthorization(authorization)
    service = ConversationService(
        engine, scoped, runs, ArtifactService(engine, store, scoped), retention=ChannelRetention()
    )
    runs.turns = service.hooks
    if cleanup is not None:
        cleanup.register("conversation", service.clean)
        for resource_type in ("message", "summary", "context"):
            cleanup.register(resource_type, service.clean_derived)
    return service
