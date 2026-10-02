"""技能版本动作映射到资源；包内容读取沿用技能授权并复核原始身份。"""

from sqlalchemy.ext.asyncio import AsyncEngine

from creativity_service.core.auth.types import ResourceState, ResourceStateReader
from creativity_service.core.context import AuthContext
from creativity_service.core.primitives import ServiceError
from creativity_service.modules.iam.authorization import IamAuthorization
from creativity_service.modules.skills.repositories import repository


class SkillResourceReader:
    def __init__(self, engine: AsyncEngine, fallback: ResourceStateReader | None) -> None:
        self.engine, self.fallback = engine, fallback

    async def read_current(
        self, context: AuthContext, resource_type: str, resource_id: str
    ) -> ResourceState | None:
        table = {"skill": "skills", "version": "resource_versions", "artifact": "artifacts"}.get(
            resource_type
        )
        if table:
            async with self.engine.connect() as connection:
                row = await repository(table, context.scope).get(connection, resource_id)
            if row and (resource_type != "version" or row["resource_type"] == "skill"):
                return ResourceState(
                    scope=context.scope,
                    resource_type=resource_type,
                    resource_id=resource_id,
                    name=row.get("name") or row.get("version_label") or "技能产物",
                    active=row.get("state") not in {"DELETED", "DELETING"},
                )
        return (
            await self.fallback.read_current(context, resource_type, resource_id)
            if self.fallback
            else None
        )


class SkillAuthorization:
    def __init__(self, engine: AsyncEngine, authorization: IamAuthorization) -> None:
        self.engine, self.authorization = engine, authorization

    async def require(self, context: AuthContext, action: str, resource_id: str) -> None:
        skill_id = resource_id
        if resource_id not in {"new", "scope", "*"}:
            async with self.engine.connect() as connection:
                row = await repository("resource_versions", context.scope).get(
                    connection, resource_id
                )
            if row and row["resource_type"] == "skill":
                skill_id = row["resource_id"]
        await self.authorization.boundary(context, action, "skill", skill_id)


class PackageAuthorization:
    """仅由技能服务创建，存储范围由已登记产物确定，不能扩大调用身份的权限。"""

    def __init__(
        self, authorization: IamAuthorization, original: AuthContext, skill_id: str, action: str
    ) -> None:
        self.authorization, self.original, self.skill_id, self.action = (
            authorization,
            original,
            skill_id,
            action,
        )

    async def require(self, context: AuthContext, action: str, resource_id: str) -> None:
        if context.scope.channel_id != self.original.scope.channel_id:
            raise ServiceError("FORBIDDEN", "包内容不能跨渠道读取", 403)
        await self.authorization.boundary(self.original, self.action, "skill", self.skill_id)
