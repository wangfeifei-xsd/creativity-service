"""技能脚本只能经固定版本工具授权进入无网络容器。"""

from creativity_service.core.context import Scope
from creativity_service.core.primitives import ServiceError, digest, utcnow
from creativity_service.integrations.sandbox import ContainerSandbox
from creativity_service.integrations.tools import AdapterRegistration, AdapterRequest, AdapterResult
from creativity_service.modules.resources.usage import record_use
from creativity_service.modules.skills.frozen import FrozenSkillPort
from creativity_service.modules.skills.services import SkillService
from creativity_service.modules.tools.schemas import ToolBinding


class ScriptAdapter:
    def __init__(self, skills: SkillService, sandbox: ContainerSandbox) -> None:
        self.skills, self.sandbox = skills, sandbox

    async def invoke(self, request: AdapterRequest) -> AdapterResult:
        binding = request.definition.binding.script
        if not binding or not request.run_id:
            raise ServiceError(
                "SCRIPT_BINDING_REQUIRED", "脚本调用须绑定受理时的技能配置和统一运行", 422
            )
        skills = (
            FrozenSkillPort(self.skills, request.frozen_skills)
            if request.frozen_skills
            else self.skills
        )
        skill = await skills.resolve(request.context, binding.skill_version_id, "runtime")
        if not skill.active or skill.state != "PUBLISHED":
            raise ServiceError("SCRIPT_VERSION_REQUIRED", "脚本须来自已发布的技能配置", 409)
        if not any(file.relative_path == binding.path for file in skill.definition.files):
            raise ServiceError("SCRIPT_NOT_FOUND", "脚本不在固定技能包内", 404)
        source = (await skills.read_files(request.context, skill, (binding.path,), "runtime"))[
            binding.path
        ]
        profile = self.sandbox.profile(
            request.context.scope.channel_id, binding.profile_id, "python"
        )
        if digest(profile.model_dump(mode="json")) != binding.profile_digest:
            raise ServiceError("SANDBOX_PROFILE_CHANGED", "隔离环境已变化，请更新工具配置", 409)
        await skills.recheck(request.context, skill, "runtime")
        await record_use(
            self.skills.engine, request.context, request.run_id, "skill", skill.skill_id
        )
        result = await self.sandbox.python(profile, source, request.arguments)
        await skills.recheck(request.context, skill, "runtime")
        return AdapterResult(
            data=result,
            source_request_id=request.attempt_id,
            source_version=skill.definition.package_hash,
            observed_at=utcnow(),
            coverage={
                "runtime_image": profile.image,
                "profile": profile.profile_id,
                "script": binding.path,
            },
        )


def resolve_script(
    skills: SkillService, sandbox: ContainerSandbox, scope: Scope, binding: ToolBinding
) -> AdapterRegistration | None:
    if binding.adapter_key != "sandbox_python":
        return None
    if not binding.script or binding.implementation_version != "1":
        raise ServiceError("SCRIPT_BINDING_REQUIRED", "隔离脚本须选择技能、文件与运行环境", 422)
    profile = sandbox.profile(scope.channel_id, binding.script.profile_id, "python")
    if digest(profile.model_dump(mode="json")) != binding.script.profile_digest:
        raise ServiceError("SANDBOX_PROFILE_CHANGED", "隔离环境配置已变化", 409)
    return AdapterRegistration(
        key="sandbox_python",
        name="隔离 Python 计算",
        source_type="sandbox",
        implementation_version="1",
        actual_effect="READ_ONLY",
        adapter=ScriptAdapter(skills, sandbox),
        channel_id=scope.channel_id,
        environment=scope.environment,
        sensitive=False,
    )
