"""技能自然语言对话、候选校验与幂等保存，生成阶段不改动技能资源。"""

import asyncio
import json
from typing import Any

from creativity_service.core.context import AuthContext, TaskEnvelope
from creativity_service.core.contracts import BusinessResult, ResultEnvelope
from creativity_service.core.database import UnitOfWork, transaction
from creativity_service.core.deletion import ContentRef, DeletionGuard, content_key
from creativity_service.core.files import FileService
from creativity_service.core.locking import ResourceKey, record_key
from creativity_service.core.observability.audit import append_audit
from creativity_service.core.primitives import ServiceError, digest
from creativity_service.modules.agents.access import locked_policy
from creativity_service.modules.agents.repositories import repository
from creativity_service.modules.agents.services import AgentService
from creativity_service.modules.iam.repositories import policy_key
from creativity_service.modules.models.configuration import resolve_routes
from creativity_service.modules.resources.configuration import require_dependencies, require_edit
from creativity_service.modules.runs.repositories import required as run_required
from creativity_service.modules.runs.schemas import AdmissionReceipt
from creativity_service.modules.runtime.admission import RuntimeAdmission
from creativity_service.modules.runtime.storage import load_spec
from creativity_service.modules.skills.assistance_catalog import published_catalog
from creativity_service.modules.skills.assistance_schemas import (
    SkillAssistanceOutput,
    SkillAssistanceRequest,
    SkillAssistanceRoute,
    SkillAssistanceSaved,
    SkillAssistanceTurn,
    SkillProposal,
)
from creativity_service.modules.skills.authorization import PackageAuthorization
from creativity_service.modules.skills.builtin import BUILTIN_ID, BUILTIN_NAME, builtin_definition
from creativity_service.modules.skills.dependencies import resolve_dependencies
from creativity_service.modules.skills.loader import PLACEHOLDER
from creativity_service.modules.skills.packages import MAX_ARCHIVE, Package, entry, validate_files
from creativity_service.modules.skills.persistence import PackageWrite
from creativity_service.modules.skills.schemas import SkillDefinition
from creativity_service.modules.skills.services import SkillService


class SkillAssistance:
    def __init__(
        self, skills: SkillService, agents: AgentService, admission: RuntimeAdmission
    ) -> None:
        self.skills, self.agents, self.admission = skills, agents, admission
        self.runs = admission.runs

    def keys(self, context: AuthContext, skill_id: str) -> list[ResourceKey]:
        return [
            content_key(context.scope),
            policy_key(context.scope.channel_id),
            policy_key("system"),
            record_key(context.scope.channel_id, "skills", skill_id),
            record_key(context.scope.channel_id, "resource_versions", skill_id),
        ]

    async def require(self, context: AuthContext, skill_id: str | None) -> None:
        if context.principal_type not in {"management", "worker"} or not context.actor_id:
            raise ServiceError("FORBIDDEN", "技能智能协助仅供管理人员使用", 403)
        await self.skills.require(context, "skill:manage", skill_id or "new")
        await self.skills.require(context, "version:edit", skill_id or "new")

    async def require_in(
        self,
        uow: UnitOfWork,
        context: AuthContext,
        skill_id: str | None,
    ) -> None:
        policy = await locked_policy(uow, context)
        for action in ("skill:manage", "version:edit"):
            policy.require(context, action, "skill", skill_id or "new")
        if skill_id:
            await require_edit(uow, context, "skill", skill_id)
            current = await repository("resource_versions", context.scope).get(
                uow.connection, skill_id
            )
            if not current or current["resource_type"] != "skill" or current["state"] == "RETIRED":
                raise ServiceError("SKILL_UNAVAILABLE", "技能当前配置不可用", 409)
            if current["state"] == "PUBLISHED":
                policy.require(context, "release:publish", "skill", skill_id)
            await DeletionGuard(context.scope).check(
                uow, [ContentRef("skill", skill_id), ContentRef("version", skill_id)]
            )

    async def routes(
        self, context: AuthContext, skill_id: str | None = None
    ) -> list[SkillAssistanceRoute]:
        await self.require(context, skill_id)
        rows = await published_catalog(self.skills, context, ("model_route",))
        return [
            SkillAssistanceRoute(route_id=row["id"], name=row["resource_name"])
            for row in rows
            if "text" in row["content"].get("required_capabilities", [])
        ]

    async def record(
        self, context: AuthContext, run_id: str
    ) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any], ResultEnvelope]:
        result = await self.runs.get_run(context, run_id)
        row = await self.runs.load(TaskEnvelope(channel_id=context.scope.channel_id, run_id=run_id))
        identity = self.runs.context(row)
        if identity.scope != context.scope or identity.principal_id != context.principal_id:
            raise ServiceError("FORBIDDEN", "只能继续当前环境中自己发起的技能智能协助", 403)
        spec = await load_spec(self.runs, row)
        descriptor = json.loads(spec.payload_json).get("runtime", {})
        if descriptor.get("kind") != "skill_assistance":
            raise ServiceError("ASSISTANCE_INVALID", "该执行不是技能智能协助", 422)
        await self.require(context, descriptor.get("skill_id"))
        async with transaction(
            self.runs.engine, context.scope, self.runs.keys(context, run_id)
        ) as uow:
            await self.runs.guard(uow, row)
            values = (
                await run_required(
                    uow.connection,
                    "run_contents",
                    context.scope.channel_id,
                    id=row["input_ref"],
                    run_id=run_id,
                )
            )["payload"]
        return row, descriptor, values, result

    async def submit(self, context: AuthContext, body: SkillAssistanceRequest) -> AdmissionReceipt:
        await self.require(context, body.skill_id)
        if not body.message.strip():
            raise ServiceError("INPUT_REQUIRED", "请描述需要创建或修改的技能", 422)
        descriptor: dict[str, Any] = {"kind": "skill_assistance", "skill_id": body.skill_id}
        values: dict[str, Any] = {
            "base": None,
            "base_package": None,
            "history": [],
            "reference_files": [],
            "references": [],
        }
        if body.previous_run_id:
            _, previous, old, result = await self.record(context, body.previous_run_id)
            if result.state != "SUCCEEDED" or result.result is None:
                raise ServiceError("ASSISTANCE_PENDING", "请等待上一轮完成后继续", 409)
            if previous.get("skill_id") != body.skill_id:
                raise ServiceError("ASSISTANCE_TARGET_CHANGED", "更换技能后请开始新的对话", 409)
            descriptor.update(previous)
            values.update({key: old[key] for key in values})
            values["history"] = [
                *old["history"],
                {"role": "user", "content": old["message"]},
                {"role": "assistant", "content": result.result.data},
            ]
            if len(values["history"]) >= 20:
                raise ServiceError(
                    "ASSISTANCE_LIMIT", "本次对话已达 10 轮，请保存或开始新对话", 422
                )
        elif body.skill_id:
            skill, current = await self.skills.raw(context, body.skill_id)
            if body.base_revision is not None and body.base_revision != current["revision"]:
                raise ServiceError("REVISION_CONFLICT", "技能已修改，请刷新当前配置后重试", 409)
            definition = SkillDefinition.model_validate(current["content"])
            package = await self.skills.package(context, body.skill_id, definition)
            if len(package.instructions) > 40000:
                raise ServiceError(
                    "ASSISTANCE_CONTEXT_LIMIT", "当前技能书超过 40000 字，请先精简内容", 422
                )
            values["base"] = SkillProposal(
                **{
                    key: skill[key]
                    for key in ("skill_code", "name", "description", "owner", "tags")
                },
                instructions=package.instructions,
                settings=package.settings,
            ).model_dump(mode="json")
            values["base_package"] = definition.model_dump(mode="json")
            values["reference_files"] = [f.model_dump(mode="json") for f in package.manifest]
            values["references"] = [
                {
                    "path": f.relative_path,
                    "text": package.files[f.relative_path].decode("utf-8-sig"),
                }
                for f in package.manifest
                if f.loadable and f.relative_path != "SKILL.md"
            ]
            descriptor.update(skill_revision=skill["revision"], base_revision=current["revision"])
        elif body.base_revision is not None:
            raise ServiceError("ASSISTANCE_INVALID", "请选择需要修改的技能", 422)
        routes = await self.routes(context, body.skill_id)
        route_id = (
            body.model_route_id
            or descriptor.get("route_id")
            or (routes[0].route_id if routes else None)
        )
        if not route_id or route_id not in {r.route_id for r in routes}:
            raise ServiceError("MODEL_ROUTE_REQUIRED", "请先发布并授权支持文本生成的模型路由", 422)
        catalog = await published_catalog(self.skills, context, ("tool",))
        tools = [
            t for t in await self.skills.tool_options(context, versions=catalog) if t.available
        ]
        agents = await self.skills.agent_options(context, limit=128)
        if len(tools) + len(agents) > 128:
            raise ServiceError(
                "ASSISTANCE_CATALOG_LIMIT", "可用资源超过智能协助目录上限（128 个）", 422
            )
        async with self.skills.engine.connect() as connection:
            rows = await repository("resource_versions", context.scope).get_many(
                connection, [route_id, *[t.version_id for t in tools]]
            )
            if {route_id, *[t.version_id for t in tools]} - rows.keys():
                raise ServiceError("REVISION_CONFLICT", "可用资源已变化，请重试", 409)
            resolved = await resolve_routes(connection, context.scope, {route_id: rows[route_id]})
            models = await repository("models", context.scope).get_many(
                connection, [m["model_id"] for m in resolved[route_id]["content"].get("models", [])]
            )
        descriptor.update(
            route_id=route_id,
            allowed_tools=[t.version_id for t in tools],
            tool_digests={t.version_id: rows[t.version_id]["content_digest"] for t in tools},
            allowed_agents=[a.agent_id for a in agents],
        )
        values.update(
            message=body.message.strip(),
            tools=[t.model_dump(mode="json") for t in tools],
            agents=[a.model_dump(mode="json") for a in agents],
        )
        capacities = [m["context_limit"] for m in models.values() if m["context_limit"] is not None]
        config = builtin_definition(route_id, min([32000, *capacities]))
        keys = self.keys(context, body.skill_id or "new")
        keys += await self.agents.dependency_keys(context, config)
        async with transaction(self.skills.engine, context.scope, keys) as uow:
            await self.require_in(uow, context, body.skill_id)
            await self.agents.dependencies.resolve(uow, context, BUILTIN_ID, config, "production")
        sources = [ContentRef("version", identifier) for identifier in rows]
        if body.skill_id:
            sources += [ContentRef("skill", body.skill_id), ContentRef("version", body.skill_id)]
        if body.previous_run_id:
            sources.append(ContentRef("run", body.previous_run_id))
        spec = await self.admission.freeze_test(
            context,
            "skill-assistance-" + digest(body.idempotency_key)[:32],
            BUILTIN_NAME,
            config,
            await self.admission.versions(context, [route_id]),
            descriptor,
            sources,
            purpose="production",
        )
        return await self.admission.submit(context, spec, values, body.idempotency_key)

    async def turn(self, context: AuthContext, run_id: str) -> SkillAssistanceTurn:
        _, _, values, result = await self.record(context, run_id)
        return SkillAssistanceTurn(
            run_id=run_id,
            state=result.state,
            state_label=result.state_label,
            reply=SkillAssistanceOutput.model_validate(result.result.model_dump(mode="json")).data
            if result.state == "SUCCEEDED" and result.result
            else None,
            error=result.error.message if result.error else None,
            base=SkillProposal.model_validate(values["base"]) if values["base"] else None,
        )

    async def package(
        self, context: AuthContext, proposal: SkillProposal, values: dict[str, Any]
    ) -> Package:
        files = {}
        metadata = {"name": proposal.name, "description": proposal.description}
        if values["base_package"]:
            base = SkillDefinition.model_validate(values["base_package"])
            original = await self.skills.package(context, values["skill_id"], base)
            files.update(original.files)
            metadata = {**original.metadata, **metadata}
        files["SKILL.md"] = (
            (
                "---\n"
                + json.dumps(metadata, ensure_ascii=False)
                + "\n---\n\n"
                + proposal.instructions
                + "\n"
            ).encode()
            if values["base_package"]
            else entry(proposal.name, proposal.description, proposal.instructions)
        )
        package = validate_files(files, proposal.settings)
        names = {v.name for v in package.settings.input_variables}
        for file in package.manifest:
            if (
                file.loadable
                and set(PLACEHOLDER.findall(package.files[file.relative_path].decode("utf-8-sig")))
                - names
            ):
                raise ServiceError(
                    "SKILL_VARIABLE_INVALID", "技能书或参考文件使用了未声明的变量", 422
                )
        if len(package.files["SKILL.md"]) > proposal.settings.context_budget:
            raise ServiceError(
                "SKILL_CONTEXT_LIMIT", "上下文上限不足以加载完整技能书，请调整加载上限", 422
            )
        return package

    async def validate_in(
        self,
        uow: UnitOfWork,
        context: AuthContext,
        proposal: SkillProposal,
        descriptor: dict[str, Any],
    ) -> list[str]:
        await self.require_in(uow, context, descriptor.get("skill_id"))
        ids = list(set(proposal.settings.tool_bindings.values()))
        if not set(ids) <= set(descriptor["allowed_tools"]):
            raise ServiceError("DEPENDENCY_INVALID", "技能引用了可用目录之外的工具", 422)
        if not set(proposal.settings.allowed_agents) <= set(descriptor["allowed_agents"]):
            raise ServiceError("DEPENDENCY_INVALID", "技能引用了可用目录之外的智能体", 422)
        policy = await locked_policy(uow, context)
        for identifier in proposal.settings.allowed_agents:
            policy.require(context, "agent:manage", "agent", identifier)
        await require_dependencies(uow, context.scope, ids)
        rows = await repository("resource_versions", context.scope).get_many(uow.connection, ids)
        await DeletionGuard(context.scope).check(
            uow,
            [
                *[ContentRef("tool", row["resource_id"]) for row in rows.values()],
                *[
                    ContentRef("agent", identifier)
                    for identifier in proposal.settings.allowed_agents
                ],
            ],
        )
        if any(
            row["content_digest"] != descriptor["tool_digests"].get(row["id"])
            for row in rows.values()
        ):
            raise ServiceError("REVISION_CONFLICT", "技能依赖的工具已修改，请重新生成", 409)
        resolved, _, issues = await resolve_dependencies(
            uow.connection, context, proposal.settings, self.skills.tools
        )
        if issues:
            raise ServiceError(
                "SKILL_DEPENDENCY_MISSING", "；".join(i.message for i in issues), 422
            )
        await self.skills.check_tool_permissions(uow, context, resolved)
        return resolved

    async def validate_result(
        self, context: AuthContext, value: Any, descriptor: dict[str, Any], values: dict[str, Any]
    ) -> BusinessResult:
        output = SkillAssistanceOutput.model_validate(value)
        proposal = output.data.proposal
        if (output.business_status == "COMPLETED") != (proposal is not None):
            raise ServiceError("ASSISTANCE_INVALID", "完整技能书与待补充状态不一致", 422)
        if proposal:
            if values["base"] and proposal.skill_code != values["base"]["skill_code"]:
                raise ServiceError("ASSISTANCE_INVALID", "修改时不能更换技能编码", 422)
            await self.package(
                context, proposal, {**values, "skill_id": descriptor.get("skill_id")}
            )
            async with transaction(
                self.skills.engine,
                context.scope,
                self.keys(context, descriptor.get("skill_id") or "new"),
            ) as uow:
                await self.validate_in(uow, context, proposal, descriptor)
        return BusinessResult.model_validate(output.model_dump(mode="json"))

    async def apply(self, context: AuthContext, run_id: str) -> SkillAssistanceSaved:
        row, descriptor, values, result = await self.record(context, run_id)
        if result.state != "SUCCEEDED" or result.result is None:
            raise ServiceError("ASSISTANCE_PENDING", "尚无可保存的完整技能书", 409)
        output = SkillAssistanceOutput.model_validate(result.result.model_dump(mode="json"))
        proposal = output.data.proposal
        if proposal is None:
            raise ServiceError("ASSISTANCE_NEEDS_INPUT", "请先补充信息生成完整技能书", 422)
        target = descriptor.get("skill_id")
        suffix = digest([context.scope.model_dump(), run_id, "skill"])[0:32]
        skill_id, audit_id = target or "skill_" + suffix, "audit_" + suffix
        keys = [
            *self.keys(context, skill_id),
            *self.runs.keys(context, run_id),
            record_key(context.scope.channel_id, "audit_events", audit_id),
            ResourceKey(context.scope.channel_id, "skill-code", (proposal.skill_code,)),
        ]
        async with transaction(self.skills.engine, context.scope, keys) as uow:
            await self.require_in(uow, context, target)
            await self.runs.guard(uow, row)
            if await self.saved_in(uow, context, skill_id, audit_id):
                return SkillAssistanceSaved(skill_id=skill_id)
            old_files = await repository("skill_files", context.scope).find(
                uow.connection, version_id=skill_id
            )
        package = await self.package(context, proposal, {**values, "skill_id": target})
        archive = await asyncio.to_thread(package.archive)
        if len(archive) > MAX_ARCHIVE:
            raise ServiceError("SKILL_FORMAT_INVALID", "技能包压缩后超过 8 MiB，请精简技能书", 422)
        uploaded = await FileService(
            self.skills.store,
            PackageAuthorization(
                self.skills.authorization,
                context,
                target or "new",
                "skill:manage",
            ),
        ).upload(context, "skill-package.zip", "application/zip", archive)
        writing = PackageWrite(skill_id, skill_id, package, uploaded.url, old_files)
        async with transaction(
            self.skills.engine, context.scope, keys + writing.keys(context.scope)
        ) as uow:
            await self.runs.guard(uow, row)
            await self.require_in(uow, context, target)
            if await self.saved_in(uow, context, skill_id, audit_id):
                return SkillAssistanceSaved(skill_id=skill_id)
            repo = repository("skills", context.scope)
            skill = await repo.get(uow.connection, skill_id)
            current = await repository("resource_versions", context.scope).get(
                uow.connection, skill_id
            )
            if target:
                if (
                    not skill
                    or not current
                    or skill["revision"] != descriptor["skill_revision"]
                    or current["revision"] != descriptor["base_revision"]
                ):
                    raise ServiceError(
                        "REVISION_CONFLICT", "技能已被修改，请开始新对话；本次技能书仍保留", 409
                    )
                if proposal.skill_code != skill["skill_code"]:
                    raise ServiceError("ASSISTANCE_INVALID", "修改时不能更换技能编码", 422)
            elif await repository("skills", context.scope, include_deleted=True).find(
                uow.connection, skill_code=proposal.skill_code
            ):
                raise ServiceError("CODE_EXISTS", "技能编码已存在，请让助手更换编码", 409)
            ids = await self.validate_in(uow, context, proposal, descriptor)
            fields = proposal.model_dump(
                mode="json", include={"name", "description", "owner", "tags"}
            )
            if target:
                assert skill is not None
                await repo.change(uow, skill_id, skill["revision"], fields)
            else:
                await repo.add(
                    uow, skill_id, {**fields, "skill_code": proposal.skill_code, "status": "ACTIVE"}
                )
            await writing.save_in(uow, context, ids, current)
            await append_audit(uow, context, audit_id, "skill.assistance.apply", "skill", skill_id)
        return SkillAssistanceSaved(skill_id=skill_id)

    async def saved_in(
        self, uow: UnitOfWork, context: AuthContext, skill_id: str, audit_id: str
    ) -> bool:
        saved = await repository("audit_events", context.scope, include_deleted=True).get(
            uow.connection, audit_id
        )
        if not saved:
            return False
        current = await repository("skills", context.scope).get(uow.connection, skill_id)
        if not current:
            raise ServiceError("CONTENT_DELETED", "此前保存的技能已删除，不能重复恢复", 409)
        await DeletionGuard(context.scope).check(
            uow, [ContentRef("skill", skill_id), ContentRef("version", skill_id)]
        )
        return True
