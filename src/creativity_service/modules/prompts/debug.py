"""冻结调试描述并对接统一运行；发布只认可受信运行和用量证据。"""

from typing import TYPE_CHECKING, Any, Literal

from creativity_service.core.context import AuthContext
from creativity_service.core.contracts import DisplayStatus, ResourceVersion
from creativity_service.core.contracts.display import display_status
from creativity_service.core.database import UnitOfWork, assert_external_io_allowed, transaction
from creativity_service.core.deletion import ContentRef, DeletionGuard, content_key
from creativity_service.core.locking import record_key
from creativity_service.core.observability.audit import append_audit
from creativity_service.core.primitives import ServiceError, digest, new_id, unavailable
from creativity_service.core.versioning import version_view
from creativity_service.modules.prompts.rendering import render, validate_content
from creativity_service.modules.prompts.repositories import repository
from creativity_service.modules.prompts.schemas import (
    PromptContent,
    PromptDebugDescriptor,
    PromptDebugEvidence,
    PromptDebugRun,
    PromptRenderRequest,
    PromptRenderView,
    PromptTestRequest,
    PromptTestView,
)

if TYPE_CHECKING:
    from creativity_service.modules.prompts.services import PromptService


def descriptor_from(row: dict[str, Any], context: AuthContext) -> PromptDebugDescriptor:
    return PromptDebugDescriptor(
        test_id=row["id"],
        scope=context.scope,
        prompt=ResourceVersion.model_validate(row["frozen_version"]),
        model_route_version=row["model_route_version"],
        sample_id=row["sample_id"],
        sample_digest=digest(row["sample_snapshot"]),
        rendered=PromptRenderView.model_validate(row["rendered_input"]),
        expected_constraints=row["sample_snapshot"]["expected_constraints"],
        descriptor_digest=row["descriptor_digest"],
    )


def verify_evidence(
    context: AuthContext, row: dict[str, Any], evidence: PromptDebugEvidence
) -> None:
    if (
        evidence.scope != context.scope
        or evidence.run_id != row["run_id"]
        or evidence.descriptor_digest != row["descriptor_digest"]
        or evidence.model_route_version != row["model_route_version"]
    ):
        raise ServiceError("PROMPT_EVIDENCE_INVALID", "调试证据与当前范围或冻结内容不符")


class PromptVersionValidator:
    def __init__(self, service: "PromptService") -> None:
        self.service = service

    async def validate(
        self,
        uow: UnitOfWork,
        context: AuthContext,
        version: ResourceVersion,
        operation: Literal["freeze", "release"],
    ) -> None:
        uow.require_scope(context.scope)
        if version.resource_type != "prompt":
            raise ServiceError("PROMPT_VERSION_INVALID", "提示词门禁只接受提示词版本")
        content = PromptContent.model_validate(version.content)
        validate_content(content)
        if version.dependency_version_ids or version.output_schema:
            raise ServiceError(
                "PROMPT_DEPENDENCY_INVALID", "提示词不能携带未声明的配置依赖或智能体输出结构"
            )
        resource = await repository("prompts", context.scope).get(
            uow.connection, version.resource_id
        )
        if resource is None or resource["status"] != "ACTIVE":
            raise ServiceError("PROMPT_UNAVAILABLE", "提示词资源不可用")
        if self.service.evidence is None:
            raise unavailable("统一调试测试证据服务")
        tests = await repository("prompt_tests", context.scope).find(
            uow.connection, version_id=version.version_id
        )
        for test in sorted(tests, key=lambda t: t["created_at"], reverse=True):
            frozen = ResourceVersion.model_validate(test["frozen_version"])
            if (
                test["status"] != "SUBMITTED"
                or not test["run_id"]
                or frozen.content_digest != version.content_digest
                or frozen.dependencies_digest != version.dependencies_digest
            ):
                continue
            await DeletionGuard(context.scope).check(
                uow,
                [
                    ContentRef("prompt_test", test["id"]),
                    ContentRef("version", test["model_route_version"]),
                ],
            )
            route = await repository("resource_versions", context.scope).get(
                uow.connection, test["model_route_version"]
            )
            if (
                route is None
                or route["state"] != "PUBLISHED"
                or route["resource_type"] != "model_route"
            ):
                continue
            evidence = await self.service.evidence.read(uow, context, test)
            if evidence is None:
                continue
            verify_evidence(context, test, evidence)
            if (
                evidence.status == "SUCCEEDED"
                and evidence.constraints_passed
                and evidence.usage_recorded
            ):
                return
        raise ServiceError("PROMPT_TEST_REQUIRED", "缺少当前内容通过测试且已记录用量的真实调试证据")


class PromptDebugService:
    def __init__(self, service: "PromptService") -> None:
        self.service = service

    async def prepare(
        self, context: AuthContext, version_id: str, body: PromptTestRequest
    ) -> PromptDebugDescriptor:
        service, scope = self.service, context.scope
        await service.authorization.require(context, "run:create", version_id)
        version = await service.read_version(context, version_id)
        if version.state == "RETIRED":
            raise ServiceError("VERSION_UNAVAILABLE", "已退役版本不能开始新调试")
        route_name = await service.require_model_route(context, body.model_route_version)
        sample = await service.sample_row(context, version.resource_id, body.sample_id)
        inputs, context_sources = await service.input_values(
            context, version, PromptRenderRequest(sample_id=body.sample_id)
        )
        # 变量错误先返回，不进入统一运行、预算或模型调用。
        rendered = render(
            PromptContent.model_validate(version.content), context, inputs, reveal_sensitive=True
        )
        test_id, audit_id = new_id("prompt_test"), new_id("audit")
        sources = [
            ContentRef("version", version_id),
            ContentRef("prompt_sample", body.sample_id),
            ContentRef("version", body.model_route_version),
            *context_sources,
        ]
        links = {
            source: digest([test_id, source.resource_type, source.resource_id])
            for source in sources
        }
        keys = [
            content_key(scope),
            record_key(scope.channel_id, "resource_versions", version_id),
            record_key(scope.channel_id, "resource_versions", body.model_route_version),
            record_key(scope.channel_id, "prompt_samples", body.sample_id),
            record_key(scope.channel_id, "prompt_tests", test_id),
            record_key(scope.channel_id, "audit_events", audit_id),
            *(record_key(scope.channel_id, "source_links", key) for key in links.values()),
        ]
        async with transaction(service.engine, scope, keys) as uow:
            await DeletionGuard(scope).check(uow, sources)
            current = await repository("resource_versions", scope).get(uow.connection, version_id)
            if (
                current is None
                or current["revision"] != body.revision
                or version.content_digest != current["content_digest"]
            ):
                raise ServiceError("REVISION_CONFLICT", "草稿已变更，请刷新后重新调试")
            fixed_sample = await repository("prompt_samples", scope).get(
                uow.connection, body.sample_id
            )
            if fixed_sample is None or fixed_sample["revision"] != sample["revision"]:
                raise ServiceError("REVISION_CONFLICT", "样例已变更，请重新调试")
            route = await repository("resource_versions", scope).get(
                uow.connection, body.model_route_version
            )
            if (
                route is None
                or route["resource_type"] != "model_route"
                or route["state"] != "PUBLISHED"
            ):
                raise ServiceError("MODEL_ROUTE_UNAVAILABLE", "请选择同渠道的已发布模型路由", 422)
            sample_snapshot = {
                "title": sample["title"],
                "input": sample["input"],
                "expected_constraints": sample["expected_constraints"],
            }
            frozen = version_view(current)
            summary = {
                "scope": scope.model_dump(mode="json"),
                "prompt": frozen.model_dump(mode="json"),
                "model_route_version": body.model_route_version,
                "sample": sample_snapshot,
                "rendered": rendered.model_dump(mode="json"),
                "purpose": "debug",
            }
            row = await repository("prompt_tests", scope).add(
                uow,
                test_id,
                {
                    "version_id": version_id,
                    "draft_revision": frozen.draft_revision,
                    "release_snapshot_id": None,
                    "model_route_version": body.model_route_version,
                    "rendered_input_ref": None,
                    "run_id": None,
                    "frozen_version": frozen.model_dump(mode="json"),
                    "sample_snapshot": sample_snapshot,
                    "rendered_input": rendered.model_dump(mode="json"),
                    "descriptor_digest": digest(summary),
                    "sample_id": body.sample_id,
                    "model_route_name": route_name,
                    "status": "PREPARED",
                },
            )
            for source, link in links.items():
                await DeletionGuard(scope).link(
                    uow, link, source, ContentRef("prompt_test", test_id)
                )
            await append_audit(
                uow,
                context,
                audit_id,
                "prompt.test.prepare",
                "prompt",
                version.resource_id,
                {"version_id": version_id, "content_digest": version.content_digest},
            )
        return descriptor_from(row, context)

    async def start(
        self, context: AuthContext, version_id: str, body: PromptTestRequest
    ) -> PromptTestView:
        descriptor = await self.prepare(context, version_id, body)
        return await self.submit(context, descriptor.test_id)

    async def submit(self, context: AuthContext, test_id: str) -> PromptTestView:
        service, scope = self.service, context.scope
        row = await self.row(context, test_id)
        await service.authorization.require(context, "run:create", row["version_id"])
        if row["run_id"] is not None:
            return await self.detail(context, test_id)
        if service.runner is None:
            raise unavailable("统一调试运行服务")
        current_version = await service.read_version(context, row["version_id"])
        if current_version.state == "RETIRED":
            raise ServiceError("VERSION_UNAVAILABLE", "已退役版本不能开始新调试")
        await service.require_model_route(context, row["model_route_version"])
        descriptor = descriptor_from(row, context)
        assert_external_io_allowed()
        # 17 必须以 test_id 幂等受理；回写失败后的重试取得同一运行，不重复扣预算。
        run: PromptDebugRun = await service.runner.submit(context, descriptor)
        link_id, audit_id = digest([test_id, "run", run.run_id]), new_id("audit")
        await service.authorization.require(context, "run:create", row["version_id"])
        async with transaction(
            service.engine,
            scope,
            [
                content_key(scope),
                record_key(scope.channel_id, "prompt_tests", test_id),
                record_key(scope.channel_id, "source_links", link_id),
                record_key(scope.channel_id, "audit_events", audit_id),
            ],
        ) as uow:
            await DeletionGuard(scope).check(uow, [ContentRef("prompt_test", test_id)])
            current = await repository("prompt_tests", scope).get(uow.connection, test_id)
            if current is None:
                raise ServiceError("NOT_FOUND", "调试记录不存在", 404)
            if current["run_id"] and current["run_id"] != run.run_id:
                raise ServiceError("PROMPT_RUN_CONFLICT", "统一运行服务未遵守调试幂等约定", 503)
            if not current["run_id"]:
                await repository("prompt_tests", scope).change(
                    uow, test_id, current["revision"], {**run.model_dump(), "status": "SUBMITTED"}
                )
                await DeletionGuard(scope).link(
                    uow, link_id, ContentRef("run", run.run_id), ContentRef("prompt_test", test_id)
                )
                await append_audit(
                    uow, context, audit_id, "prompt.test.submit", "prompt_test", test_id
                )

        return await self.detail(context, test_id)

    async def row(self, context: AuthContext, test_id: str) -> dict[str, Any]:
        async with transaction(
            self.service.engine, context.scope, [content_key(context.scope)]
        ) as uow:
            await DeletionGuard(context.scope).check(uow, [ContentRef("prompt_test", test_id)])
            row = await repository("prompt_tests", context.scope).get(uow.connection, test_id)
        if row is None:
            raise ServiceError("NOT_FOUND", "调试记录不存在", 404)
        await self.service.authorization.require(context, "version:read", row["version_id"])
        return row

    async def detail(
        self, context: AuthContext, test_id: str, reveal: bool = False
    ) -> PromptTestView:
        from creativity_service.modules.prompts.services import redacted_content

        service = self.service
        row = await self.row(context, test_id)
        frozen = ResourceVersion.model_validate(row["frozen_version"])
        if reveal:
            await service.authorization.require(context, "data:read_sensitive", frozen.resource_id)
            await service.authorization.require(context, "run:content", frozen.resource_id)
        evidence = None
        if service.evidence and row["run_id"]:
            async with transaction(
                service.engine, context.scope, [content_key(context.scope)]
            ) as uow:
                await DeletionGuard(context.scope).check(uow, [ContentRef("prompt_test", test_id)])
                evidence = await service.evidence.read(uow, context, row)
                if evidence:
                    verify_evidence(context, row, evidence)
        rendered = PromptRenderView.model_validate(row["rendered_input"])
        if not reveal:
            # 样例整体属于敏感数据，不依赖变量作者准确标注才能阻止原文外泄。
            rendered = rendered.model_copy(
                update={
                    "sections": [
                        s.model_copy(update={"text": "••••••"}) for s in rendered.sections
                    ],
                    "masked": True,
                }
            )
            frozen = frozen.model_copy(
                update={
                    "content": redacted_content(
                        PromptContent.model_validate(frozen.content)
                    ).model_dump(mode="json")
                }
            )
        status = (
            display_status(evidence.status)
            if evidence
            else DisplayStatus(
                value=row["status"],
                label="待执行" if row["status"] == "PREPARED" else "已受理",
                tone="default",
            )
        )
        return PromptTestView(
            test_id=test_id,
            version_id=row["version_id"],
            version_label=frozen.version_label,
            draft_revision=row["draft_revision"],
            model_route_version=row["model_route_version"],
            model_route_name=row["model_route_name"],
            sample_title=row["sample_snapshot"]["title"],
            run_id=row["run_id"],
            status=status,
            created_at=row["created_at"],
            snapshot=frozen,
            rendered=rendered,
            output=evidence.output if evidence and reveal else None,
            masked=not reveal,
            descriptor_digest=row["descriptor_digest"],
        )

    async def list_items(self, context: AuthContext, version_id: str) -> list[PromptTestView]:
        await self.service.read_version(context, version_id)
        async with self.service.engine.connect() as connection:
            rows = await repository("prompt_tests", context.scope).find(
                connection, version_id=version_id
            )
        return [
            await self.detail(context, row["id"])
            for row in sorted(rows, key=lambda r: r["created_at"], reverse=True)
        ]

    async def read_descriptor(self, context: AuthContext, test_id: str) -> PromptDebugDescriptor:
        row = await self.row(context, test_id)
        await self.service.authorization.require(context, "run:create", row["version_id"])
        return descriptor_from(row, context)
