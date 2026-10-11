"""智能协助的渠道目录、对话快照与候选草稿原子保存。"""

import json
import re
from typing import Any

from creativity_service.core.context import AuthContext, TaskEnvelope
from creativity_service.core.contracts import ResultEnvelope
from creativity_service.core.database import UnitOfWork, transaction
from creativity_service.core.deletion import ContentRef, DeletionGuard
from creativity_service.core.locking import ResourceKey
from creativity_service.core.observability.audit import append_audit
from creativity_service.core.primitives import ServiceError, canonical_json, digest
from creativity_service.modules.agents.access import locked_require
from creativity_service.modules.agents.assistance_schemas import (
    AssistanceOutput,
    AssistanceRequest,
    AssistanceSaved,
    AssistanceTurn,
)
from creativity_service.modules.agents.builtin import (
    BUILTIN_CODE,
    BUILTIN_ID,
    BUILTIN_NAME,
    builtin_definition,
)
from creativity_service.modules.agents.repositories import dependency_rows, repository, required
from creativity_service.modules.agents.schemas import AgentCreate, AgentDefinition
from creativity_service.modules.agents.services import AgentService
from creativity_service.modules.agents.validation import static_issues
from creativity_service.modules.runs.repositories import required as run_required
from creativity_service.modules.runs.schemas import AdmissionReceipt
from creativity_service.modules.runtime.admission import RuntimeAdmission
from creativity_service.modules.runtime.storage import load_spec


def version_fingerprint(rows: list[dict[str, Any]]) -> str:
    return digest(sorted([r["id"], r["revision"], r["state"]] for r in rows))


def named_fields(schema: Any) -> bool:
    if not isinstance(schema, dict):
        return True
    for field in schema.get("properties", {}).values():
        if not isinstance(field, dict) or not re.search(
            r"[\u3400-\u9fff]", str(field.get("title", ""))
        ):
            return False
    return all(named_fields(value) for value in schema.values() if isinstance(value, dict)) and all(
        named_fields(item) for value in schema.values() if isinstance(value, list) for item in value
    )


class AgentAssistance:
    def __init__(self, agents: AgentService, admission: RuntimeAdmission) -> None:
        self.agents, self.admission, self.runs = agents, admission, admission.runs

    async def require(self, context: AuthContext, agent_id: str | None) -> None:
        if context.principal_type not in {"management", "worker"} or not context.actor_id:
            raise ServiceError("FORBIDDEN", "智能协助仅供管理人员使用", 403)
        if agent_id == BUILTIN_ID:
            raise ServiceError("BUILTIN_IMMUTABLE", "内置智能体由平台维护，不能修改", 403)
        await self.agents.require(context, "agent:manage", agent_id or "new")

    async def record(
        self, context: AuthContext, run_id: str
    ) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any], ResultEnvelope]:
        result = await self.runs.get_run(context, run_id)
        row = await self.runs.load(TaskEnvelope(channel_id=context.scope.channel_id, run_id=run_id))
        identity = self.runs.context(row)
        if identity.scope != context.scope or identity.principal_id != context.principal_id:
            raise ServiceError("FORBIDDEN", "只能继续当前环境中自己发起的智能协助", 403)
        spec = await load_spec(self.runs, row)
        descriptor = json.loads(spec.payload_json).get("runtime", {})
        if descriptor.get("kind") != "agent_assistance":
            raise ServiceError("ASSISTANCE_INVALID", "该执行不是智能协助", 422)
        await self.require(context, descriptor.get("agent_id"))
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

    async def submit(self, context: AuthContext, body: AssistanceRequest) -> AdmissionReceipt:
        await self.require(context, body.agent_id)
        if not body.message.strip():
            raise ServiceError("INPUT_REQUIRED", "请描述需要创建或修改的内容", 422)
        descriptor: dict[str, Any] = {"kind": "agent_assistance", "agent_id": body.agent_id}
        history: list[dict[str, Any]] = []
        base: dict[str, Any] | None = None
        if body.previous_run_id:
            _, previous, values, result = await self.record(context, body.previous_run_id)
            if result.state != "SUCCEEDED" or result.result is None:
                raise ServiceError("ASSISTANCE_PENDING", "请等待上一轮完成后继续", 409)
            if previous.get("agent_id") != body.agent_id:
                raise ServiceError("ASSISTANCE_TARGET_CHANGED", "更换智能体后请开始新的对话", 409)
            descriptor.update(previous)
            base = values["base"]
            history = [
                *values["history"],
                {"role": "user", "content": values["message"]},
                {"role": "assistant", "content": result.result.data},
            ]
            if len(history) > 20:
                raise ServiceError(
                    "ASSISTANCE_LIMIT", "本次对话已达 10 轮，请保存方案或开始新的对话", 422
                )
        elif body.agent_id:
            async with transaction(
                self.agents.engine, context.scope, self.agents.keys(context, body.agent_id)
            ) as uow:
                await locked_require(uow, context, "agent:manage", "agent", body.agent_id)
                agent = await required(uow.connection, context.scope, "agents", body.agent_id)
                versions = await repository("resource_versions", context.scope).find(
                    uow.connection, resource_type="agent", resource_id=body.agent_id
                )
                candidates = [v for v in versions if v["state"] == "DRAFT"] or versions
                version = (
                    next((v for v in versions if v["id"] == body.base_version_id), None)
                    if body.base_version_id
                    else max(candidates, key=lambda v: v["created_at"], default=None)
                )
                if version is None:
                    raise ServiceError("NOT_FOUND", "所选智能体版本不存在", 404)
                descriptor.update(
                    agent_revision=agent["revision"],
                    base_version_id=version["id"],
                    versions_digest=version_fingerprint(versions),
                )
                base = {k: agent[k] for k in ("agent_code", "name", "description", "owner")}
                base["definition"] = version["content"]
                base["version_label"] = "智能协助草稿"
        elif body.base_version_id:
            raise ServiceError("ASSISTANCE_INVALID", "请选择需要修改的智能体", 422)

        options = await self.agents.options(context)
        routes = [
            d
            for d in options.dependencies
            if d.resource_type == "model_route" and "text" in d.required_capabilities
        ]
        route_id = (
            body.model_route_id
            or descriptor.get("route_id")
            or (routes[0].version_id if routes else None)
        )
        if route_id not in {r.version_id for r in routes}:
            raise ServiceError(
                "MODEL_ROUTE_REQUIRED", "请先发布并授权一个支持文本生成的模型路由", 422
            )
        descriptor["route_id"] = route_id
        dependencies = options.dependencies
        if len(dependencies) > 128:
            raise ServiceError(
                "ASSISTANCE_CATALOG_LIMIT", "可用资源超过智能协助目录上限（128 个）", 422
            )
        async with self.agents.engine.connect() as connection:
            rows = await repository("resource_versions", context.scope).get_many(
                connection, [d.version_id for d in dependencies]
            )
        catalog = []
        for dependency in dependencies:
            content = rows[dependency.version_id]["content"]
            item = dependency.model_dump(mode="json")
            item["configuration"] = {
                k: content[k]
                for k in (
                    "input_schema",
                    "output_schema",
                    "timeout_seconds",
                    "variables",
                    "input_variables",
                    "required_tool_versions",
                    "required_model_capabilities",
                    "allowed_agents",
                )
                if k in content
            }
            if dependency.resource_type == "model_route":
                item["configuration"]["max_output_tokens"] = max(
                    (
                        int(model.get("parameters", {}).get("max_tokens", 1024))
                        for model in content.get("models", [])
                    ),
                    default=1024,
                )
            catalog.append(item)
        descriptor["allowed_resources"] = [d.version_id for d in dependencies]
        descriptor["resource_digests"] = {
            identifier: row["content_digest"] for identifier, row in rows.items()
        }
        values = {
            "message": body.message.strip(),
            "base": base,
            "history": history,
            "resources": catalog,
            "example": options.templates[0].definition.model_dump(mode="json", by_alias=True),
        }
        if len(canonical_json(values)) > 160000:
            raise ServiceError(
                "ASSISTANCE_CONTEXT_LIMIT", "当前配置与对话内容过长，请开始新的对话", 422
            )
        async with self.agents.engine.connect() as connection:
            models = await repository("models", context.scope).get_many(
                connection, [m["model_id"] for m in rows[route_id]["content"].get("models", [])]
            )
        capacities = [m["context_limit"] for m in models.values()]
        context_limit = min([32000, *[c for c in capacities if c is not None]])
        config = builtin_definition(route_id, context_limit)
        keys = self.agents.keys(context, body.agent_id or "new")
        keys += await self.agents.dependency_keys(context, config)
        async with transaction(self.agents.engine, context.scope, keys) as uow:
            await locked_require(uow, context, "agent:manage", "agent", body.agent_id or "new")
            await self.agents.dependencies.resolve(uow, context, BUILTIN_ID, config, "production")
        frozen_versions = await self.admission.versions(context, [route_id])
        sources = [ContentRef("version", d.version_id) for d in dependencies]
        if body.agent_id:
            sources += [
                ContentRef("agent", body.agent_id),
                ContentRef("version", descriptor["base_version_id"]),
            ]
        if body.previous_run_id:
            sources.append(ContentRef("run", body.previous_run_id))
        spec = await self.admission.freeze_test(
            context,
            body.idempotency_key,
            BUILTIN_NAME,
            config,
            frozen_versions,
            descriptor,
            sources,
            purpose="production",
        )
        return await self.admission.submit(context, spec, values, body.idempotency_key)

    async def turn(self, context: AuthContext, run_id: str) -> AssistanceTurn:
        _, _, values, result = await self.record(context, run_id)
        return AssistanceTurn(
            run_id=run_id,
            state=result.state,
            state_label=result.state_label,
            reply=AssistanceOutput.model_validate(result.result.model_dump(mode="json")).data
            if result.state == "SUCCEEDED" and result.result
            else None,
            error=result.error.message if result.error else None,
            base_definition=AgentDefinition.model_validate(values["base"]["definition"])
            if values["base"]
            else None,
        )

    async def validate_in(
        self,
        uow: UnitOfWork,
        context: AuthContext,
        proposal: AgentCreate,
        descriptor: dict[str, Any],
    ) -> None:
        if proposal.agent_code == BUILTIN_CODE:
            raise ServiceError("BUILTIN_IMMUTABLE", "内置智能体不能修改", 403)
        definition = proposal.definition
        issues = static_issues(definition)
        if issues:
            raise ServiceError("FLOW_INVALID", "；".join(i.message for i in issues[:6]), 422)
        schemas = [
            definition.input_schema,
            definition.output_schema,
            *[schema for s in definition.steps for schema in (s.input_schema, s.output_schema)],
        ]
        if not all(named_fields(schema) for schema in schemas):
            raise ServiceError("FIELD_NAME_REQUIRED", "输入输出的全部字段需要显示名称", 422)
        if not set(definition.dependency_ids()) <= set(descriptor["allowed_resources"]):
            raise ServiceError("DEPENDENCY_INVALID", "候选方案引用了资源目录之外的依赖", 422)
        loaded = await dependency_rows(uow.connection, context.scope, definition.dependency_ids())
        if any(
            row["content_digest"] != descriptor["resource_digests"].get(row["id"])
            for row in loaded
            if row["id"] in definition.dependency_ids()
        ):
            raise ServiceError("REVISION_CONFLICT", "方案依赖的资源已变更，请重新生成方案", 409)
        content = definition.model_dump(mode="json")
        check, _, _ = await self.agents.inspect_in(
            uow,
            context,
            {"id": descriptor.get("agent_id") or "new", "status": "ACTIVE"},
            {
                "content": content,
                "revision": 1,
                "output_schema": definition.output_schema,
                "content_digest": digest(
                    {"content": content, "output_schema": definition.output_schema}
                ),
            },
            "production",
            require_evaluation=False,
            loaded_dependencies=loaded,
        )
        if not check.valid:
            raise ServiceError(
                "ASSISTANCE_INVALID",
                "；".join(
                    issue.message for c in check.checks if not c.passed for issue in c.issues
                ),
                422,
            )

    async def validate(
        self,
        context: AuthContext,
        output: AssistanceOutput,
        descriptor: dict[str, Any],
        values: dict[str, Any],
    ) -> None:
        proposal = output.data.proposal
        if (output.business_status == "COMPLETED") != (proposal is not None):
            raise ServiceError("ASSISTANCE_INVALID", "完整方案与待补充状态不一致", 422)
        if proposal is None:
            return
        if values["base"] and proposal.agent_code != values["base"]["agent_code"]:
            raise ServiceError("ASSISTANCE_INVALID", "修改时不能更换智能体调用编码", 422)
        keys = self.agents.keys(context, descriptor.get("agent_id") or "new")
        keys += await self.agents.dependency_keys(context, proposal.definition)
        async with transaction(self.agents.engine, context.scope, keys) as uow:
            await locked_require(
                uow, context, "agent:manage", "agent", descriptor.get("agent_id") or "new"
            )
            await self.validate_in(uow, context, proposal, descriptor)

    async def apply(self, context: AuthContext, run_id: str) -> AssistanceSaved:
        row, descriptor, values, result = await self.record(context, run_id)
        if result.state != "SUCCEEDED" or result.result is None:
            raise ServiceError("ASSISTANCE_PENDING", "尚无可保存的完整方案", 409)
        output = AssistanceOutput.model_validate(result.result.model_dump(mode="json"))
        proposal = output.data.proposal
        if proposal is None:
            raise ServiceError("ASSISTANCE_NEEDS_INPUT", "请先补充信息生成完整方案", 422)
        target = descriptor.get("agent_id")
        suffix = digest([context.scope.model_dump(), run_id])[:32]
        agent_id, version_id, audit_id = (
            target or "agent_" + suffix,
            "version_" + suffix,
            "audit_" + suffix,
        )
        keys = self.agents.keys(
            context,
            agent_id,
            ("resource_versions", version_id),
            ("audit_events", audit_id),
            ("source_links", digest([version_id, "agent", agent_id])),
        )
        keys += self.runs.keys(context, run_id)
        keys += await self.agents.dependency_keys(context, proposal.definition)
        keys.append(ResourceKey(context.scope.channel_id, "agent-code", (proposal.agent_code,)))
        async with transaction(self.agents.engine, context.scope, keys) as uow:
            await locked_require(uow, context, "agent:manage", "agent", target or "new")
            await self.runs.guard(uow, row)
            existing = await repository(
                "resource_versions", context.scope, include_deleted=True
            ).get(uow.connection, version_id)
            if existing:
                if existing["is_deleted"]:
                    raise ServiceError("CONTENT_DELETED", "此前保存的草稿已删除，不能重复恢复", 409)
                await DeletionGuard(context.scope).check(
                    uow, [ContentRef("agent", agent_id), ContentRef("version", version_id)]
                )
                return AssistanceSaved(agent_id=agent_id, version_id=version_id)
            repo = repository("agents", context.scope)
            if target:
                agent = await required(uow.connection, context.scope, "agents", target)
                versions = await repository("resource_versions", context.scope).find(
                    uow.connection, resource_type="agent", resource_id=target
                )
                if (
                    agent["revision"] != descriptor["agent_revision"]
                    or version_fingerprint(versions) != descriptor["versions_digest"]
                ):
                    raise ServiceError(
                        "REVISION_CONFLICT",
                        "智能体已被修改，请开始新对话生成方案；本次方案仍保留",
                        409,
                    )
                if proposal.agent_code != agent["agent_code"]:
                    raise ServiceError("ASSISTANCE_INVALID", "修改时不能更换智能体调用编码", 422)
            else:
                if await repository("agents", context.scope, include_deleted=True).find(
                    uow.connection, agent_code=proposal.agent_code
                ):
                    raise ServiceError(
                        "AGENT_CODE_CONFLICT", "调用编码已存在，请让助手更换编码", 409
                    )
            await self.validate_in(uow, context, proposal, descriptor)
            fields = proposal.model_dump(exclude={"definition", "version_label", "agent_code"})
            if target:
                await repo.change(uow, target, agent["revision"], fields)
            else:
                await repo.add(
                    uow, agent_id, {**fields, "agent_code": proposal.agent_code, "status": "ACTIVE"}
                )
            label = proposal.version_label
            if target:
                labels = {v["version_label"] for v in versions}
                number = 1
                while label in labels:
                    number += 1
                    label = f"{proposal.version_label[:50]}（{number}）"
            await self.agents.add_draft(
                uow, context, agent_id, version_id, label, proposal.definition
            )
            await append_audit(uow, context, audit_id, "agent.assistance.apply", "agent", agent_id)
        return AssistanceSaved(agent_id=agent_id, version_id=version_id)
