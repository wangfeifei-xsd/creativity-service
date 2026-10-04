"""正式、调试与评测共同受理；冻结测试描述只由服务器内的模块端口创建。"""

import copy
import json
from typing import Any, cast

from creativity_service.core.context import AuthContext
from creativity_service.core.contracts import ResourceVersion
from creativity_service.core.database import Repository, UnitOfWork, transaction
from creativity_service.core.database.tables import metadata
from creativity_service.core.deletion import (
    CONFIG_CONTENT_TYPES,
    ContentRef,
    DeletionGuard,
    content_key,
)
from creativity_service.core.locking import ResourceKey, record_key
from creativity_service.core.primitives import (
    RunInput,
    ServiceError,
    canonical_json,
    digest,
    utcnow,
)
from creativity_service.core.versioning import version_view
from creativity_service.integrations.models.contracts import ModelRequest
from creativity_service.modules.agents.runtime import AgentRunResolver, PreparedAgentResolver
from creativity_service.modules.agents.schemas import AgentDefinition, FrozenExecutionSpec, Purpose
from creativity_service.modules.agents.services import AgentService
from creativity_service.modules.models.schemas import FrozenModel
from creativity_service.modules.runs.schemas import AdmissionReceipt, ResolvedDefinition
from creativity_service.modules.runs.services import RunService
from creativity_service.modules.runtime.model import plan_for
from creativity_service.modules.runtime.storage import load_spec


class RuntimeResolver(AgentRunResolver):
    async def prepare(
        self, context: AuthContext, request: RunInput
    ) -> tuple[ResolvedDefinition, PreparedAgentResolver]:
        definition, resolver = await super().prepare(context, request)
        return with_estimate(definition, request.input), resolver

    async def resolve(self, context: AuthContext, request: RunInput) -> ResolvedDefinition:
        definition = await super().resolve(context, request)
        assert definition.frozen_spec is not None
        return with_estimate(definition, request.input)


def with_estimate(definition: ResolvedDefinition, values: dict[str, Any]) -> ResolvedDefinition:
    spec = definition.frozen_spec
    assert spec is not None
    model: FrozenModel | None = None
    for version in spec.versions:
        if version.resource_type == "model_route":
            model = FrozenModel.model_validate(
                cast(list[dict[str, Any]], version.content["models"])[0]
            )
            break
    payload = json.loads(spec.payload_json)
    if model is None and payload.get("runtime", {}).get("model"):
        model = FrozenModel.model_validate(payload["runtime"]["model"])
    if model is None:
        return definition
    plan = plan_for(
        spec,
        model,
        ModelRequest(
            messages=[{"role": "user", "content": json.dumps(values, ensure_ascii=False)}]
        ),
        "pending",
    )
    return definition.model_copy(update={"admission_plan": plan})


class FrozenResolver:
    def __init__(self, agents: AgentService, definition: ResolvedDefinition) -> None:
        self.agents, self.definition_value = agents, definition

    async def resolve(self, context: AuthContext, request: RunInput) -> ResolvedDefinition:
        return self.definition_value

    def keys(self, context: AuthContext, definition: ResolvedDefinition) -> list[ResourceKey]:
        assert definition.frozen_spec is not None
        if definition.frozen_spec.versions[0].resource_type == "agent":
            return AgentRunResolver(self.agents).keys(context, definition)
        return [
            content_key(context.scope),
            *[
                record_key(context.scope.channel_id, "resource_versions", v)
                for v in definition.version_ids
            ],
        ]

    async def validate_in(
        self, uow: UnitOfWork, context: AuthContext, definition: ResolvedDefinition
    ) -> None:
        spec = definition.frozen_spec
        if spec is None or spec.scope != context.scope:
            raise ServiceError("SNAPSHOT_INVALID", "冻结测试范围不符", 403)
        if spec.versions[0].resource_type == "agent":
            await AgentRunResolver(self.agents).validate_in(uow, context, definition)
            return
        if (
            spec.purpose not in {"debug", "evaluation"}
            and not (
                spec.purpose == "production"
                and json.loads(spec.payload_json).get("runtime", {}).get("kind") == "memory"
            )
            or spec.versions[0].resource_type != "runtime"
        ):
            raise ServiceError("SNAPSHOT_INVALID", "测试描述用途不符", 403)
        payload = json.loads(spec.payload_json)
        if spec.versions[0].content["descriptor_digest"] != digest(
            [payload["runtime"], payload["source_refs"]]
        ):
            raise ServiceError("SNAPSHOT_INVALID", "测试描述摘要不符", 403)
        repo = Repository(metadata.tables["resource_versions"], context.scope)
        for version in spec.versions:
            row = await repo.get(uow.connection, version.version_id)
            if row is None or version_view(row) != version:
                raise ServiceError("REVISION_CONFLICT", "测试依赖发生变化，请重新测试", 409)
        await DeletionGuard(context.scope).check(
            uow, [ContentRef("version", v.version_id) for v in spec.versions]
        )


class RuntimeAdmission:
    def __init__(self, runs: RunService, agents: AgentService) -> None:
        self.runs, self.agents = runs, agents
        runs.resolver = RuntimeResolver(agents)
        runs.rerun_handler = self.rerun

    async def rerun(
        self, context: AuthContext, row: dict[str, Any], request: RunInput, key: str
    ) -> AdmissionReceipt:
        if row["purpose"] == "evaluation":
            from creativity_service.modules.evaluations.repositories import (
                repository as evaluations,
            )

            async with self.runs.engine.connect() as connection:
                entries = await evaluations("evaluation_results", context.scope).find(
                    connection, run_id=row["id"]
                )
            if entries:
                raise ServiceError(
                    "EVALUATION_RERUN_REQUIRED", "请从评测报告重跑样本以保留固定数据和预算关联", 422
                )
        spec = await load_spec(self.runs, row)
        if spec.versions[0].resource_type == "agent":
            # 保留调试/评测用途；当前依赖和授权重新冻结，原运行内容保持不变。
            _, current = await self.agents.raw(context, spec.source_version_id)
            spec = await self.agents.freeze_candidate(
                context, spec.source_version_id, current["revision"], spec.purpose
            )
        else:
            payload = json.loads(spec.payload_json)
            if payload["runtime"].get("kind") == "memory":
                raise ServiceError(
                    "MEMORY_RETRY_REQUIRED", "请从后台记忆整理重试以保留来源批次", 422
                )
            descriptor = {**payload["runtime"], "report_test": False}
            spec = await self.freeze_test(
                context,
                digest([row["id"], key]),
                row["agent_name"],
                spec.definition,
                list(spec.versions[1:]),
                descriptor,
                [ContentRef(*r) for r in payload["source_refs"]],
            )
            request = request.model_copy(update={"agent_code": spec.agent_code})
        scoped = copy.copy(self.runs)
        scoped.resolver = FrozenResolver(
            self.agents, with_estimate(AgentRunResolver.definition(spec), request.input)
        )
        return await scoped.admit_run(context, request, key, parent_run_id=row["id"])

    async def submit(
        self, context: AuthContext, spec: FrozenExecutionSpec, values: dict[str, Any], key: str
    ) -> AdmissionReceipt:
        if spec.scope != context.scope:
            raise ServiceError("SCOPE_MISMATCH", "运行快照范围不符", 403)
        definition = with_estimate(AgentRunResolver.definition(spec), values)
        scoped = copy.copy(self.runs)
        scoped.resolver = FrozenResolver(self.agents, definition)
        return await scoped.admit_run(
            context, RunInput(agent_code=spec.agent_code, input=values), key
        )

    async def freeze_test(
        self,
        context: AuthContext,
        key: str,
        name: str,
        config: AgentDefinition,
        versions: list[ResourceVersion],
        descriptor: dict[str, Any],
        sources: list[ContentRef],
        *,
        purpose: Purpose = "debug",
    ) -> FrozenExecutionSpec:
        await self.runs.authorization.require(context, "run:create", "new")
        if purpose == "production" and descriptor.get("kind") != "memory":
            raise ServiceError("SNAPSHOT_INVALID", "正式内部运行仅接受后台记忆整理", 403)
        identifier = digest([context.scope.model_dump(), context.principal_id, key])
        version_id = "runtime_" + identifier[:48]
        source_refs = [[s.resource_type, s.resource_id] for s in sources]
        # 配置版本只保留执行结构与摘要；样例和个人内容只写入受删除传播保护的运行内容。
        content = {
            "definition": config.model_dump(mode="json"),
            "descriptor_digest": digest([descriptor, source_refs]),
        }
        deps = tuple(v.version_id for v in versions)
        source_links = [
            (digest([version_id, r.resource_type, r.resource_id]), r)
            for r in sources
            if r.resource_type in CONFIG_CONTENT_TYPES
        ]
        keys = [
            content_key(context.scope),
            record_key(context.scope.channel_id, "resource_versions", version_id),
            *[record_key(context.scope.channel_id, "source_links", i) for i, _ in source_links],
        ]
        async with transaction(self.runs.engine, context.scope, keys) as uow:
            repo = Repository(metadata.tables["resource_versions"], context.scope)
            old = await repo.get(uow.connection, version_id)
            if old:
                root = version_view(old)
                if root.content != content:
                    raise ServiceError("IDEMPOTENCY_CONFLICT", "同一测试描述已经固定", 409)
            else:
                root = version_view(
                    await repo.add(
                        uow,
                        version_id,
                        {
                            "resource_type": "runtime",
                            "resource_id": identifier,
                            "version_label": name,
                            "state": "PUBLISHED" if purpose == "production" else "DRAFT",
                            "content": content,
                            "content_digest": digest(
                                {"content": content, "output_schema": config.output_schema}
                            ),
                            "dependencies": list(deps),
                            "dependencies_digest": digest(list(deps)),
                            "output_schema": config.output_schema,
                            "created_by": context.actor_id or context.principal_id,
                        },
                    )
                )
            guard = DeletionGuard(context.scope)
            for link, source in source_links:
                await guard.check(uow, [source])
                if not await Repository(metadata.tables["source_links"], context.scope).get(
                    uow.connection, link
                ):
                    await guard.link(uow, link, source, ContentRef("version", version_id))
        frozen = (root, *versions)
        return FrozenExecutionSpec(
            snapshot_id=version_id,
            scope=context.scope,
            agent_id=identifier,
            agent_code=version_id,
            agent_name=name,
            source_version_id=version_id,
            source_revision=1,
            purpose=purpose,
            content_digest=root.content_digest,
            dependencies_digest=digest([v.model_dump(mode="json") for v in frozen]),
            candidate_digest=digest(content),
            captured_at=old["created_at"] if old else utcnow(),
            payload_json=canonical_json(
                {
                    "definition": config.model_dump(mode="json"),
                    "versions": [v.model_dump(mode="json") for v in frozen],
                    "runtime": descriptor,
                    "source_refs": source_refs,
                }
            ).decode(),
        )

    async def versions(self, context: AuthContext, ids: list[str]) -> list[ResourceVersion]:
        result: dict[str, ResourceVersion] = {}
        pending = list(ids)
        async with transaction(
            self.runs.engine, context.scope, [content_key(context.scope)]
        ) as uow:
            repo = Repository(metadata.tables["resource_versions"], context.scope)
            while pending:
                identifier = pending.pop(0)
                if identifier in result:
                    continue
                row = await repo.get(uow.connection, identifier)
                if row is None or row["state"] == "RETIRED":
                    raise ServiceError("DEPENDENCY_INVALID", "测试依赖不可用", 422)
                await DeletionGuard(context.scope).check(uow, [ContentRef("version", identifier)])
                version = version_view(row)
                result[identifier] = version
                pending.extend(version.dependency_version_ids)
        return list(result.values())
