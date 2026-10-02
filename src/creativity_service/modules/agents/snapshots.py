"""发布与候选共用检查链；冻结 JSON 与评测摘要独立于最终分配的版本标识。"""

from typing import Any

from jsonschema import Draft202012Validator

from creativity_service.core.context import AuthContext
from creativity_service.core.database import UnitOfWork, transaction
from creativity_service.core.deletion import ContentRef, DeletionGuard
from creativity_service.core.locking import record_key
from creativity_service.core.primitives import (
    ServiceError,
    canonical_json,
    digest,
    new_id,
    unavailable,
    utcnow,
)
from creativity_service.core.versioning import version_view
from creativity_service.modules.agents.access import locked_require
from creativity_service.modules.agents.base import AgentKernel
from creativity_service.modules.agents.dependencies import dependency_manifest
from creativity_service.modules.agents.repositories import repository, required
from creativity_service.modules.agents.schemas import (
    AgentCheck,
    AgentDefinition,
    AgentIssue,
    AgentTestInput,
    AgentTestView,
    AgentValidateInput,
    AgentValidation,
    FrozenExecutionSpec,
    Purpose,
)
from creativity_service.modules.agents.validation import static_issues
from creativity_service.modules.releases.checks import check_budget, check_evidence


class AgentSnapshots(AgentKernel):
    async def inspect_in(
        self,
        uow: UnitOfWork,
        context: AuthContext,
        agent: dict[str, Any],
        version: dict[str, Any],
        purpose: Purpose,
        refs: tuple[str, ...] = (),
        *,
        require_evaluation: bool = True,
    ) -> tuple[AgentValidation, list[dict[str, Any]], dict[str, Any]]:
        definition = AgentDefinition.model_validate(version["content"])
        issues = static_issues(definition)
        checks = [
            AgentCheck(key="authorization", label="当前授权与环境", passed=True),
            AgentCheck(key="flow", label="结构与流程终止", passed=not issues, issues=issues),
        ]
        if issues:
            return (
                AgentValidation(
                    valid=False,
                    revision=version["revision"],
                    content_digest=version["content_digest"],
                    dependencies_digest=None,
                    candidate_digest=None,
                    checks=checks,
                    dependencies=[],
                ),
                [],
                {},
            )
        rows: list[dict[str, Any]] = []
        manifest: dict[str, Any] = {}
        dependencies_digest = candidate_digest = None
        try:
            environment_state = await repository("agent_environment_states", context.scope).get(
                uow.connection, self.mapping_id(context, agent["id"])
            )
            if agent["status"] != "ACTIVE" or (
                environment_state and environment_state["status"] != "ACTIVE"
            ):
                raise ServiceError("AGENT_DISABLED", "智能体已下线或停用", 422)
            if version["content_digest"] != digest(
                {"content": version["content"], "output_schema": version["output_schema"]}
            ):
                raise ServiceError("SNAPSHOT_INVALID", "智能体内容摘要不一致", 409)
            if version["output_schema"] != definition.output_schema:
                raise ServiceError("SNAPSHOT_INVALID", "智能体输出契约不一致", 409)
            rows, policies, models = await self.dependencies.resolve(
                uow, context, agent["id"], definition, purpose
            )
            checks.append(AgentCheck(key="dependencies", label="依赖、权限与模型能力", passed=True))
            policies["budgets"] = await check_budget(
                self.budgets, uow, context, agent["id"], definition, models
            )
            checks.append(AgentCheck(key="budget", label="可执行预算", passed=True))
            manifest = dependency_manifest(rows, policies)
            dependencies_digest = digest(manifest)
            candidate_digest = digest(
                {"content": version["content_digest"], "dependencies": dependencies_digest}
            )
            if (
                require_evaluation
                and purpose == "production"
                and context.scope.environment == "prod"
            ):
                evidence = (
                    await self.evaluation.read(uow, context, refs)
                    if self.evaluation and refs
                    else None
                )
                check_evidence(
                    evidence,
                    context,
                    agent["id"],
                    version["content_digest"],
                    dependencies_digest,
                    refs,
                )
                assert evidence is not None
                # 报告摘要记录于检查证据，不混入被评测内容摘要，避免循环依赖。
                checks.append(
                    AgentCheck(
                        key="evaluation",
                        label="生产评测证据",
                        passed=True,
                        issues=[AgentIssue(code="EVIDENCE", message=evidence.report_digest)],
                    )
                )
        except ServiceError as exc:
            stage = (
                "evaluation"
                if exc.code.startswith("EVALUATION")
                else "budget"
                if exc.code.startswith("BUDGET")
                else "dependencies"
            )
            checks.append(
                AgentCheck(
                    key=stage,
                    label={
                        "evaluation": "生产评测证据",
                        "budget": "可执行预算",
                        "dependencies": "依赖、权限与模型能力",
                    }[stage],
                    passed=False,
                    issues=[AgentIssue(code=exc.code, message=exc.message)],
                )
            )
        views = [await self.dependency_view(uow, context, row) for row in rows]
        return (
            AgentValidation(
                valid=all(c.passed for c in checks),
                revision=version["revision"],
                content_digest=version["content_digest"],
                dependencies_digest=dependencies_digest,
                candidate_digest=candidate_digest,
                checks=checks,
                dependencies=views,
            ),
            rows,
            manifest,
        )

    @staticmethod
    def require_valid(validation: AgentValidation) -> None:
        issues = [
            issue for check in validation.checks if not check.passed for issue in check.issues
        ]
        if issues:
            raise ServiceError(issues[0].code, "；".join(issue.message for issue in issues), 422)

    async def validate(
        self, context: AuthContext, version_id: str, body: AgentValidateInput
    ) -> AgentValidation:
        agent, version = await self.raw(context, version_id)
        await self.require(context, "agent:manage", agent["id"])
        keys = self.keys(context, agent["id"], ("resource_versions", version_id))
        if self.evaluation and body.evaluation_refs:
            keys += self.evaluation.keys(context, body.evaluation_refs)
        # 缺依赖也要返回可阅读的校验结果，不在枚举锁时提前丢失检查清单。
        try:
            keys += await self.dependency_keys(
                context, AgentDefinition.model_validate(version["content"])
            )
        except ServiceError:
            pass
        async with transaction(self.engine, context.scope, keys) as uow:
            agent, version = await self.locked_version(
                uow, context, agent["id"], version_id, body.revision, "agent:manage"
            )
            result, _, _ = await self.inspect_in(
                uow, context, agent, version, body.purpose, body.evaluation_refs
            )
            return result

    async def freeze_candidate(
        self, context: AuthContext, version_id: str, revision: int, purpose: Purpose = "debug"
    ) -> FrozenExecutionSpec:
        if purpose == "production":
            raise ServiceError("PUBLISHED_MAPPING_REQUIRED", "正式运行必须解析环境发布映射", 422)
        agent, version = await self.raw(context, version_id)
        await self.require(context, "agent:manage", agent["id"])
        await self.require(context, "run:create", agent["id"])
        identifier = new_id("candidate")
        keys = self.keys(
            context,
            agent["id"],
            ("resource_versions", version_id),
            ("agent_candidates", identifier),
        )
        dependency_keys = await self.dependency_keys(
            context, AgentDefinition.model_validate(version["content"])
        )
        keys += dependency_keys
        keys += self.candidate_keys(
            context, identifier, agent["id"], version["id"], dependency_keys
        )
        async with transaction(self.engine, context.scope, keys) as uow:
            agent, version = await self.locked_version(
                uow, context, agent["id"], version_id, revision, "agent:manage"
            )
            await locked_require(uow, context, "run:create", "agent", agent["id"])
            validation, dependencies, manifest = await self.inspect_in(
                uow, context, agent, version, purpose
            )
            self.require_valid(validation)
            return await self.store_candidate(
                uow,
                context,
                identifier,
                agent,
                version,
                purpose,
                validation,
                dependencies,
                manifest,
            )

    async def store_candidate(
        self,
        uow: UnitOfWork,
        context: AuthContext,
        identifier: str,
        agent: dict[str, Any],
        version: dict[str, Any],
        purpose: Purpose,
        validation: AgentValidation,
        dependencies: list[dict[str, Any]],
        manifest: dict[str, Any],
    ) -> FrozenExecutionSpec:
        assert validation.dependencies_digest and validation.candidate_digest
        spec = FrozenExecutionSpec(
            snapshot_id=identifier,
            scope=context.scope,
            agent_id=agent["id"],
            agent_code=agent["agent_code"],
            agent_name=agent["name"],
            source_version_id=version["id"],
            source_revision=version["revision"],
            purpose=purpose,
            content_digest=validation.content_digest,
            dependencies_digest=validation.dependencies_digest,
            candidate_digest=validation.candidate_digest,
            captured_at=utcnow(),
            payload_json=canonical_json(
                {
                    "definition": version["content"],
                    "manifest": manifest,
                    "versions": [
                        version_view(v).model_dump(mode="json") for v in [version, *dependencies]
                    ],
                }
            ).decode(),
        )
        await repository("agent_candidates", context.scope).add(
            uow,
            identifier,
            {
                "agent_id": agent["id"],
                "source_version_id": version["id"],
                "source_revision": version["revision"],
                "purpose": purpose,
                "content_digest": validation.content_digest,
                "dependencies_digest": validation.dependencies_digest,
                "candidate_digest": validation.candidate_digest,
                "spec": spec.model_dump(mode="json"),
            },
        )
        for kind, source in [
            ("agent", agent["id"]),
            *[("version", row["id"]) for row in [version, *dependencies]],
        ]:
            link_id = digest([identifier, kind, source])
            if record_key(context.scope.channel_id, "source_links", link_id) not in uow.keys:
                raise ServiceError("REVISION_CONFLICT", "候选依赖已变化，请重新冻结", 409)
            await DeletionGuard(context.scope).link(
                uow, link_id, ContentRef(kind, source), ContentRef("agent_candidate", identifier)
            )
        return spec

    async def resolve_published(self, context: AuthContext, agent_code: str) -> FrozenExecutionSpec:
        async with self.engine.connect() as connection:
            found = await repository("agents", context.scope).find(
                connection, agent_code=agent_code
            )
        if len(found) != 1:
            raise ServiceError("NOT_FOUND", "智能体不存在", 404)
        agent = found[0]
        await self.require(context, "run:create", agent["id"])
        mapping_id = self.mapping_id(context, agent["id"])
        async with self.engine.connect() as connection:
            mapping = await repository("release_mappings", context.scope).get(
                connection, mapping_id
            )
            if not mapping:
                raise ServiceError("AGENT_NOT_RELEASED", "智能体尚未发布到当前环境", 409)
            version = await required(
                connection, context.scope, "resource_versions", mapping["version_id"]
            )
        identifier = new_id("candidate")
        keys = self.keys(
            context,
            agent["id"],
            ("resource_versions", version["id"]),
            ("release_mappings", mapping_id),
            ("agent_candidates", identifier),
        )
        dependency_keys = await self.dependency_keys(
            context, AgentDefinition.model_validate(version["content"])
        )
        keys += dependency_keys
        keys += self.candidate_keys(
            context, identifier, agent["id"], version["id"], dependency_keys
        )
        async with transaction(self.engine, context.scope, keys) as uow:
            current = await repository("release_mappings", context.scope).get(
                uow.connection, mapping_id
            )
            if current != mapping:
                raise ServiceError("REVISION_CONFLICT", "环境映射已变更，请重新受理", 409)
            agent, version = await self.locked_version(
                uow, context, agent["id"], version["id"], version["revision"], "run:create"
            )
            if version["state"] != "PUBLISHED":
                raise ServiceError("AGENT_NOT_RELEASED", "正式运行只能使用已发布版本", 409)
            # 受理复查授权和依赖；评测证据在发布时留存，运行不重复要求报告提交。
            validation, dependencies, manifest = await self.inspect_in(
                uow, context, agent, version, "production", require_evaluation=False
            )
            self.require_valid(validation)
            if version["dependencies_digest"] != validation.dependencies_digest:
                raise ServiceError("DEPENDENCY_INVALID", "发布依赖或策略已变化，需要重新发布", 409)
            return await self.store_candidate(
                uow,
                context,
                identifier,
                agent,
                version,
                "production",
                validation,
                dependencies,
                manifest,
            )

    async def load_candidate(self, context: AuthContext, identifier: str) -> FrozenExecutionSpec:
        async with self.engine.connect() as connection:
            row = await required(connection, context.scope, "agent_candidates", identifier)
        await self.require(context, "run:create", row["agent_id"])
        async with transaction(
            self.engine,
            context.scope,
            self.keys(context, row["agent_id"], ("agent_candidates", identifier)),
        ) as uow:
            await locked_require(uow, context, "run:create", "agent", row["agent_id"])
            await DeletionGuard(context.scope).check(
                uow, [ContentRef("agent_candidate", identifier)]
            )
            row = await required(uow.connection, context.scope, "agent_candidates", identifier)
            spec = FrozenExecutionSpec.model_validate(row["spec"])
            if spec.scope != context.scope:
                raise ServiceError("SCOPE_MISMATCH", "候选快照范围不符", 403)
            await DeletionGuard(context.scope).check(
                uow,
                [
                    ContentRef("agent", spec.agent_id),
                    *[ContentRef("version", v.version_id) for v in spec.versions],
                ],
            )
        return spec

    async def check_external_boundary(
        self, context: AuthContext, spec: FrozenExecutionSpec
    ) -> None:
        """17 在每次外部调用前使用；普通下线不篡改历史快照，紧急停用立即阻断后续调用。"""
        stored = await self.load_candidate(context, spec.snapshot_id)
        if stored != spec:
            raise ServiceError("SNAPSHOT_INVALID", "执行快照字段不能被调用方替换", 409)
        async with self.engine.connect() as connection:
            agent = await required(connection, context.scope, "agents", spec.agent_id)
            state = await repository("agent_environment_states", context.scope).get(
                connection, self.mapping_id(context, spec.agent_id)
            )
        if agent["status"] == "EMERGENCY_STOP" or (state and state["status"] == "EMERGENCY_STOP"):
            raise ServiceError("AGENT_EMERGENCY_STOP", "智能体已紧急停用，禁止后续外部调用", 403)

    async def test(
        self, context: AuthContext, version_id: str, body: AgentTestInput
    ) -> AgentTestView:
        spec = await self.freeze_candidate(context, version_id, body.revision)
        if not Draft202012Validator(spec.definition.input_schema).is_valid(body.input):
            raise ServiceError("INPUT_SCHEMA_INVALID", "输入不符合智能体结构要求", 422)
        if self.runner is None:
            raise unavailable("智能体调试运行服务")
        return await self.runner.submit(context, spec, body)
