"""运行加载技能时使用已受理内容，当前状态与权限仍由服务端复核。"""

from creativity_service.core.context import AuthContext
from creativity_service.core.contracts import ResourceVersion
from creativity_service.core.database import Repository
from creativity_service.core.primitives import ServiceError
from creativity_service.modules.iam.reading import require_action, resource_state
from creativity_service.modules.skills.loader import ResolvedSkill
from creativity_service.modules.skills.schemas import SkillDefinition
from creativity_service.modules.skills.services import SkillService
from creativity_service.modules.tools.schemas import ToolDefinition
from creativity_service.modules.tools.tables import metadata as tool_metadata


class FrozenSkillPort:
    def __init__(self, service: SkillService, versions: tuple[ResourceVersion, ...]) -> None:
        self.service = service
        self.versions = {v.version_id: v for v in versions}

    async def resolve(self, context: AuthContext, version_id: str, purpose: str) -> ResolvedSkill:
        version = self.versions.get(version_id)
        if (
            version is None
            or version.resource_type != "skill"
            or version.channel_id != context.scope.channel_id
        ):
            raise ServiceError("SNAPSHOT_INVALID", "技能不在本次运行快照中", 403)
        resource, _ = await self.service.raw(context, version_id)
        await self.service.require(
            context, "run:create" if purpose == "runtime" else "skill:manage", resource["id"]
        )
        definition = SkillDefinition.model_validate(version.content)
        dependencies = [self.versions.get(i) for i in definition.required_tool_versions]
        if any(
            tool is None
            or tool.resource_type != "tool"
            or tool.channel_id != context.scope.channel_id
            for tool in dependencies
        ):
            raise ServiceError("SNAPSHOT_INVALID", "技能工具依赖不在运行快照中", 403)
        policy = await self.service.authorization.read_policy(context)
        async with self.service.engine.connect() as connection:
            rows = await Repository(tool_metadata.tables["tools"], context.scope).get_many(
                connection, [tool.resource_id for tool in dependencies if tool]
            )
        for tool in dependencies:
            if tool is None:
                continue
            row = rows.get(tool.resource_id)
            if row is None or row["status"] != "ACTIVE":
                raise ServiceError("SKILL_DEPENDENCY_MISSING", "技能依赖工具已不可用", 409)
            actions = policy.actions("tool", row["id"], resource_state(context, "tool", row))
            require_action(actions, "run:create")
            for action in ToolDefinition.model_validate(tool.content).required_scopes:
                require_action(actions, action)
        return ResolvedSkill(
            resource["id"],
            version_id,
            version.configuration_revision or version.draft_revision or 1,
            version.state,
            resource["status"] == "ACTIVE",
            definition,
        )

    async def read_files(
        self, context: AuthContext, skill: ResolvedSkill, paths: tuple[str, ...], purpose: str
    ) -> dict[str, bytes]:
        return await self.service.read_files(context, skill, paths, purpose)

    async def recheck(self, context: AuthContext, skill: ResolvedSkill, purpose: str) -> None:
        current = await self.resolve(context, skill.version_id, purpose)
        if not current.active:
            raise ServiceError("SKILL_UNAVAILABLE", "技能已不可用", 409)
        await self.service.artifact_context(context, skill.definition.artifact_id)
