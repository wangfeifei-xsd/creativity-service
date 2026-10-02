"""将版本动作映射到所属提示词授权；保留其他模块的资源读取链。"""

from sqlalchemy.ext.asyncio import AsyncEngine

from creativity_service.core.auth.types import ResourceState, ResourceStateReader
from creativity_service.core.context import AuthContext, Authorization
from creativity_service.core.primitives import ServiceError
from creativity_service.modules.iam.authorization import IamAuthorization
from creativity_service.modules.prompts.repositories import repository


class PromptResourceReader:
    def __init__(self, engine: AsyncEngine, fallback: ResourceStateReader | None) -> None:
        self.engine, self.fallback = engine, fallback

    async def read_current(
        self, context: AuthContext, resource_type: str, resource_id: str
    ) -> ResourceState | None:
        table = {
            "prompt": "prompts",
            "version": "resource_versions",
            "artifact": "artifacts",
            "snapshot": "release_snapshots",
        }.get(resource_type)
        if table:
            async with self.engine.connect() as connection:
                row = await repository(table, context.scope).get(connection, resource_id)
            if row is not None and (resource_type != "version" or row["resource_type"] == "prompt"):
                return ResourceState(
                    scope=context.scope,
                    resource_type=resource_type,
                    resource_id=resource_id,
                    name=row.get("name") or row.get("version_label") or "提示词产物",
                    active=row.get("state") not in {"DELETED", "DELETING"},
                )
        if self.fallback:
            return await self.fallback.read_current(context, resource_type, resource_id)
        return None


class PromptAuthorization:
    def __init__(self, engine: AsyncEngine, authorization: Authorization) -> None:
        self.engine, self.authorization = engine, authorization

    async def require(self, context: AuthContext, action: str, resource_id: str) -> None:
        if not isinstance(self.authorization, IamAuthorization):
            await self.authorization.require(context, action, resource_id)
            return
        if action.startswith("artifact:"):
            await self.authorization.require(context, action, resource_id)
            return
        prompt_id = resource_id
        if resource_id not in {"new", "scope", "*"}:
            async with self.engine.connect() as connection:
                version = await repository("resource_versions", context.scope).get(
                    connection, resource_id
                )
                if version and version["resource_type"] == "prompt":
                    prompt_id = version["resource_id"]
        decision = await self.authorization.check(context, action, "prompt", prompt_id)
        if not decision.allowed:
            raise ServiceError("FORBIDDEN", "无权执行此操作", 403)
