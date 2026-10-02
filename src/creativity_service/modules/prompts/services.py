"""提示词业务入口：范围、权限、并发、内容校验和版本依赖均由服务层处理。"""

import re
from typing import Any

from pydantic import ValidationError
from sqlalchemy.ext.asyncio import AsyncEngine

from creativity_service.core.artifacts import ArtifactService
from creativity_service.core.context import AuthContext, Authorization
from creativity_service.core.contracts import Artifact, ResourceVersion, VisibleAction
from creativity_service.core.contracts.display import display_status
from creativity_service.core.database import assert_external_io_allowed, transaction
from creativity_service.core.deletion import ContentRef, DeletionGuard, content_key
from creativity_service.core.locking import ResourceKey, record_key
from creativity_service.core.observability.audit import append_audit
from creativity_service.core.primitives import (
    ServiceError,
    canonical_json,
    digest,
    new_id,
    unavailable,
)
from creativity_service.core.versioning import VersionService
from creativity_service.modules.iam.authorization import IamAuthorization
from creativity_service.modules.prompts.authorization import PromptAuthorization
from creativity_service.modules.prompts.differences import compare_content
from creativity_service.modules.prompts.portable import export_text, import_text
from creativity_service.modules.prompts.ports import (
    PromptContextProvider,
    PromptDebugRunner,
    PromptEvidenceReader,
)
from creativity_service.modules.prompts.rendering import render, validate_content
from creativity_service.modules.prompts.repositories import repository
from creativity_service.modules.prompts.schemas import (
    PromptCompareView,
    PromptContent,
    PromptCreate,
    PromptDependencySummary,
    PromptDraftCreate,
    PromptDraftEdit,
    PromptImportRequest,
    PromptListView,
    PromptPortable,
    PromptReference,
    PromptReleaseRequest,
    PromptReleaseView,
    PromptRenderRequest,
    PromptRenderView,
    PromptRouteOption,
    PromptRuntimeInput,
    PromptSampleCreate,
    PromptSampleView,
    PromptUpdate,
    PromptVersionView,
    PromptView,
)

ENVIRONMENTS = {"dev": "开发", "test": "测试", "fat": "验收", "prod": "生产"}
ACTION_LABELS = {
    "prompt:manage": "编辑提示词",
    "version:edit": "编辑草稿",
    "version:read": "查看版本",
    "run:create": "调试",
    "release:publish": "发布",
    "data:export": "导出",
    "data:read_sensitive": "查看原文",
}


def redacted_content(content: PromptContent) -> PromptContent:
    return content.model_copy(
        update={
            "variables": [
                variable.model_copy(update={"default": None})
                if variable.sensitivity in {"sensitive", "secret"}
                else variable
                for variable in content.variables
            ]
        }
    )


class PromptService:
    def __init__(
        self,
        engine: AsyncEngine,
        authorization: Authorization,
        artifacts: ArtifactService | None = None,
        *,
        runner: PromptDebugRunner | None = None,
        evidence: PromptEvidenceReader | None = None,
        context_provider: PromptContextProvider | None = None,
    ) -> None:
        self.engine = engine
        self.authorization = PromptAuthorization(engine, authorization)
        self.artifacts, self.runner, self.evidence = artifacts, runner, evidence
        self.context_provider = context_provider
        from creativity_service.modules.prompts.debug import PromptVersionValidator

        self.versions = VersionService(engine, self.authorization, PromptVersionValidator(self))

    async def allowed(self, context: AuthContext, action: str, resource_id: str) -> bool:
        try:
            await self.authorization.require(context, action, resource_id)
            return True
        except ServiceError as exc:
            if exc.status in {403, 404}:
                return False
            raise

    async def actions(self, context: AuthContext, resource_id: str) -> list[VisibleAction]:
        return [
            VisibleAction(action_key=action, label=label)
            for action, label in ACTION_LABELS.items()
            if await self.allowed(context, action, resource_id)
        ]

    async def _resource(self, context: AuthContext, prompt_id: str) -> dict[str, Any]:
        async with self.engine.connect() as connection:
            row = await repository("prompts", context.scope).get(connection, prompt_id)
        if row is None:
            raise ServiceError("NOT_FOUND", "提示词不存在", 404)
        return row

    async def create(self, context: AuthContext, body: PromptCreate) -> PromptView:
        assert_external_io_allowed()
        await self.authorization.require(context, "prompt:manage", "new")
        if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_.-]{0,63}", body.prompt_code):
            raise ServiceError(
                "PROMPT_CODE_INVALID",
                "提示词编码须以字母开头，仅包含字母、数字、点、下划线或短横线",
                422,
            )
        if not body.name.strip() or not body.purpose.strip():
            raise ServiceError("PROMPT_INVALID", "名称和用途不能为空", 422)
        scope, prompt_id, audit_id = context.scope, new_id("prompt"), new_id("audit")
        repo = repository("prompts", scope)
        keys = [
            content_key(scope),
            ResourceKey(scope.channel_id, "prompt-code", (body.prompt_code,)),
            record_key(scope.channel_id, "prompts", prompt_id),
            record_key(scope.channel_id, "audit_events", audit_id),
        ]
        async with transaction(self.engine, scope, keys) as uow:
            await DeletionGuard(scope).check(uow, [ContentRef("prompt", prompt_id)])
            if await repo.find(uow.connection, prompt_code=body.prompt_code):
                raise ServiceError("PROMPT_CODE_CONFLICT", "提示词编码已存在")
            await repo.add(
                uow,
                prompt_id,
                {**body.model_dump(), "owner": context.principal_id, "status": "ACTIVE"},
            )
            await append_audit(uow, context, audit_id, "prompt.create", "prompt", prompt_id)
        return await self.detail(context, prompt_id)

    async def update(self, context: AuthContext, prompt_id: str, body: PromptUpdate) -> PromptView:
        await self.authorization.require(context, "prompt:manage", prompt_id)
        if not body.name.strip() or not body.purpose.strip():
            raise ServiceError("PROMPT_INVALID", "名称和用途不能为空", 422)
        scope, audit_id = context.scope, new_id("audit")
        async with transaction(
            self.engine,
            scope,
            [
                content_key(scope),
                record_key(scope.channel_id, "prompts", prompt_id),
                record_key(scope.channel_id, "audit_events", audit_id),
            ],
        ) as uow:
            await DeletionGuard(scope).check(uow, [ContentRef("prompt", prompt_id)])
            await repository("prompts", scope).change(
                uow, prompt_id, body.revision, {"name": body.name, "purpose": body.purpose}
            )
            await append_audit(uow, context, audit_id, "prompt.edit", "prompt", prompt_id)
        return await self.detail(context, prompt_id)

    async def list_items(self, context: AuthContext) -> PromptListView:
        async with self.engine.connect() as connection:
            rows = await repository("prompts", context.scope).find(connection)
        items = [
            await self.detail(context, row["id"])
            for row in sorted(rows, key=lambda r: r["created_at"], reverse=True)
            if await self.allowed(context, "version:read", row["id"])
        ]
        return PromptListView(items=items, actions=await self.actions(context, "new"))

    async def detail(self, context: AuthContext, prompt_id: str) -> PromptView:
        await self.authorization.require(context, "version:read", prompt_id)
        row = await self._resource(context, prompt_id)
        async with self.engine.connect() as connection:
            versions = await repository("resource_versions", context.scope).find(
                connection, resource_type="prompt", resource_id=prompt_id
            )
            tests = await repository("prompt_tests", context.scope).find(connection)
        versions.sort(key=lambda item: item["created_at"], reverse=True)
        ids = {v["id"] for v in versions}
        dates = [test["created_at"] for test in tests if test["version_id"] in ids]
        refs = await self.references(context, prompt_id)
        return PromptView(
            prompt_id=prompt_id,
            prompt_code=row["prompt_code"],
            name=row["name"],
            purpose=row["purpose"],
            revision=row["revision"],
            version_label=versions[0]["version_label"] if versions else None,
            status=display_status(versions[0]["state"] if versions else "DRAFT"),
            releases=await self.releases(context, prompt_id),
            last_test_at=max(dates) if dates else None,
            agent_count=len(
                {ref.source_resource_id for ref in refs if ref.resource_type_label == "智能体"}
            ),
            actions=await self.actions(context, prompt_id),
        )

    async def validate_edit(
        self, context: AuthContext, prompt_id: str, content: PromptContent
    ) -> None:
        validate_content(content)
        if any(
            v.default is not None and v.sensitivity in {"sensitive", "secret"}
            for v in content.variables
        ):
            await self.authorization.require(context, "data:read_sensitive", prompt_id)

    async def create_draft(
        self, context: AuthContext, prompt_id: str, body: PromptDraftCreate
    ) -> PromptVersionView:
        await self.authorization.require(context, "prompt:manage", prompt_id)
        await self._resource(context, prompt_id)
        await self.validate_edit(context, prompt_id, body.content)
        if not body.version_label.strip():
            raise ServiceError("VERSION_LABEL_INVALID", "版本名称不能为空", 422)
        version = await self.versions.create_draft(
            context,
            "prompt",
            prompt_id,
            body.version_label,
            body.content.model_dump(mode="json"),
            [],
            {},
        )
        return await self.version_detail(context, version.version_id)

    async def read_version(self, context: AuthContext, version_id: str) -> ResourceVersion:
        version = await self.versions.read_version(context, version_id)
        if version.resource_type != "prompt":
            raise ServiceError("NOT_FOUND", "提示词版本不存在", 404)
        await self._resource(context, version.resource_id)
        return version

    async def version_detail(self, context: AuthContext, version_id: str) -> PromptVersionView:
        version = await self.read_version(context, version_id)
        row = await self._resource(context, version.resource_id)
        content = PromptContent.model_validate(version.content)
        actions = await self.actions(context, version.resource_id)
        if not await self.allowed(context, "data:read_sensitive", version.resource_id):
            if any(
                v.default is not None and v.sensitivity in {"sensitive", "secret"}
                for v in content.variables
            ):
                actions = [a for a in actions if a.action_key != "version:edit"]
            version = version.model_copy(
                update={"content": redacted_content(content).model_dump(mode="json")}
            )
        async with self.engine.connect() as connection:
            raw = await repository("resource_versions", context.scope).get(connection, version_id)
        if raw is None:
            raise ServiceError("NOT_FOUND", "提示词版本不存在", 404)
        return PromptVersionView(
            version=version,
            name=row["name"],
            revision=version.draft_revision or raw["revision"],
            status=display_status(version.state),
            actions=actions,
        )

    async def list_versions(self, context: AuthContext, prompt_id: str) -> list[PromptVersionView]:
        await self.authorization.require(context, "version:read", prompt_id)
        await self._resource(context, prompt_id)
        async with self.engine.connect() as connection:
            rows = await repository("resource_versions", context.scope).find(
                connection, resource_type="prompt", resource_id=prompt_id
            )
        return [
            await self.version_detail(context, row["id"])
            for row in sorted(rows, key=lambda r: r["created_at"], reverse=True)
        ]

    async def edit_draft(
        self, context: AuthContext, version_id: str, body: PromptDraftEdit
    ) -> PromptVersionView:
        version = await self.read_version(context, version_id)
        await self.authorization.require(context, "prompt:manage", version.resource_id)
        await self.validate_edit(context, version.resource_id, body.content)
        old = PromptContent.model_validate(version.content)
        if any(
            v.default is not None and v.sensitivity in {"sensitive", "secret"}
            for v in old.variables
        ):
            await self.authorization.require(context, "data:read_sensitive", version.resource_id)
        await self.versions.edit_draft(
            context, version_id, body.revision, body.content.model_dump(mode="json"), [], {}
        )
        return await self.version_detail(context, version_id)

    async def input_values(
        self, context: AuthContext, version: ResourceVersion, body: PromptRenderRequest
    ) -> tuple[PromptRuntimeInput, tuple[ContentRef, ...]]:
        values = body.input
        if body.sample_id:
            if values:
                raise ServiceError("PROMPT_SAMPLE_INVALID", "固定样例不能同时覆盖输入", 422)
            sample = await self.sample_row(context, version.resource_id, body.sample_id)
            values = sample["input"]
        inputs = PromptRuntimeInput(input=values)
        sources: tuple[ContentRef, ...] = ()
        if self.context_provider:
            supplied, sources = await self.context_provider.resolve(context, inputs)
            if supplied.input != inputs.input:
                raise ServiceError("CONTEXT_OVERRIDE", "上下文服务不能改写调用输入", 503)
            inputs = supplied
        if (inputs.tool or inputs.memory) and not sources:
            raise unavailable("上下文来源追溯服务")
        return inputs, sources

    async def preview(
        self, context: AuthContext, version_id: str, body: PromptRenderRequest
    ) -> PromptRenderView:
        version = await self.read_version(context, version_id)
        if body.reveal_sensitive:
            await self.authorization.require(context, "data:read_sensitive", version.resource_id)
        inputs, sources = await self.input_values(context, version, body)
        if sources:
            async with transaction(self.engine, context.scope, [content_key(context.scope)]) as uow:
                await DeletionGuard(context.scope).check(uow, list(sources))
        view = render(
            PromptContent.model_validate(version.content),
            context,
            inputs,
            reveal_sensitive=body.reveal_sensitive,
            max_preview_chars=body.max_preview_chars,
        )
        if not body.reveal_sensitive:
            protected = {"tool", "memory"} | ({"input"} if body.sample_id else set())
            if any(section.source in protected for section in view.sections):
                view = view.model_copy(
                    update={
                        "sections": [
                            section.model_copy(update={"text": "••••••"})
                            if section.source in protected
                            else section
                            for section in view.sections
                        ],
                        "masked": True,
                    }
                )
        return view

    async def runtime_render(
        self, context: AuthContext, version: ResourceVersion, inputs: PromptRuntimeInput
    ) -> PromptRenderView:
        """16/17 传入已读取的具体版本或冻结快照；绝不从环境映射重新解析已有依赖。"""
        await self.authorization.require(context, "run:create", version.resource_id)
        if version.channel_id != context.scope.channel_id or version.resource_type != "prompt":
            raise ServiceError("NOT_FOUND", "提示词版本不存在", 404)
        expected = digest({"content": version.content, "output_schema": version.output_schema})
        if version.content_digest != expected:
            raise ServiceError("SNAPSHOT_INVALID", "提示词快照摘要不一致", 409)
        async with transaction(self.engine, context.scope, [content_key(context.scope)]) as uow:
            await DeletionGuard(context.scope).check(
                uow, [ContentRef("version", version.version_id)]
            )
        return render(
            PromptContent.model_validate(version.content), context, inputs, reveal_sensitive=True
        )

    async def dependency_summary(
        self, context: AuthContext, version_id: str
    ) -> PromptDependencySummary:
        version = await self.read_version(context, version_id)
        if version.state != "PUBLISHED":
            raise ServiceError("VERSION_UNAVAILABLE", "智能体依赖必须绑定已发布版本")
        content = PromptContent.model_validate(version.content)
        return PromptDependencySummary(
            version_id=version_id,
            content_digest=version.content_digest,
            dependencies_digest=version.dependencies_digest,
            variables=redacted_content(content).variables,
            output_requirements=content.instruction_blocks.output_requirements,
        )

    async def create_sample(
        self, context: AuthContext, prompt_id: str, body: PromptSampleCreate
    ) -> PromptSampleView:
        await self.authorization.require(context, "prompt:manage", prompt_id)
        await self._resource(context, prompt_id)
        if not context.scope.data_scope_id:
            raise ServiceError("DATA_SCOPE_REQUIRED", "保存样例必须选择业务数据域", 403)
        if (
            not body.title.strip()
            or len(canonical_json(body.input)) > 500000
            or any(len(v) > 2000 for v in body.expected_constraints)
        ):
            raise ServiceError("PROMPT_SAMPLE_INVALID", "请检查样例名称、输入大小和输出约束", 422)
        scope, sample_id, audit_id = context.scope, new_id("prompt_sample"), new_id("audit")
        link_id = digest([sample_id, prompt_id])
        async with transaction(
            self.engine,
            scope,
            [
                content_key(scope),
                record_key(scope.channel_id, "prompt_samples", sample_id),
                record_key(scope.channel_id, "source_links", link_id),
                record_key(scope.channel_id, "audit_events", audit_id),
            ],
        ) as uow:
            await DeletionGuard(scope).check(uow, [ContentRef("prompt", prompt_id)])
            row = await repository("prompt_samples", scope).add(
                uow, sample_id, {"prompt_id": prompt_id, **body.model_dump(mode="json")}
            )
            await DeletionGuard(scope).link(
                uow,
                link_id,
                ContentRef("prompt", prompt_id),
                ContentRef("prompt_sample", sample_id),
            )
            await append_audit(uow, context, audit_id, "prompt.sample.create", "prompt", prompt_id)
        return PromptSampleView(
            sample_id=sample_id,
            title=row["title"],
            input=None,
            expected_constraints=None,
            created_at=row["created_at"],
            masked=True,
        )

    async def sample_row(
        self, context: AuthContext, prompt_id: str, sample_id: str
    ) -> dict[str, Any]:
        async with transaction(self.engine, context.scope, [content_key(context.scope)]) as uow:
            await DeletionGuard(context.scope).check(uow, [ContentRef("prompt_sample", sample_id)])
            row = await repository("prompt_samples", context.scope).get(uow.connection, sample_id)
        if row is None or row["prompt_id"] != prompt_id:
            raise ServiceError("NOT_FOUND", "样例不存在", 404)
        return row

    async def samples(
        self, context: AuthContext, prompt_id: str, reveal: bool = False
    ) -> list[PromptSampleView]:
        await self.authorization.require(context, "version:read", prompt_id)
        if reveal:
            await self.authorization.require(context, "data:read_sensitive", prompt_id)
        async with self.engine.connect() as connection:
            rows = await repository("prompt_samples", context.scope).find(
                connection, prompt_id=prompt_id
            )
        result = []
        for row in rows:
            try:
                row = await self.sample_row(context, prompt_id, row["id"])
            except ServiceError as exc:
                if exc.code == "CONTENT_DELETED":
                    continue
                raise
            result.append(
                PromptSampleView(
                    sample_id=row["id"],
                    title=row["title"],
                    input=row["input"] if reveal else None,
                    expected_constraints=row["expected_constraints"] if reveal else None,
                    created_at=row["created_at"],
                    masked=not reveal,
                )
            )
        return result

    async def references(self, context: AuthContext, prompt_id: str) -> list[PromptReference]:
        await self.authorization.require(context, "version:read", prompt_id)
        async with self.engine.connect() as connection:
            versions = await repository("resource_versions", context.scope).find(
                connection, resource_type="prompt", resource_id=prompt_id
            )
            ids = {v["id"]: v for v in versions}
            refs = await repository("resource_references", context.scope).find(
                connection, target_resource_type="prompt"
            )
            result = []
            for ref in refs:
                if ref["target_version_id"] not in ids:
                    continue
                source = await repository("resource_versions", context.scope).get(
                    connection, ref["source_version_id"]
                )
                if source:
                    result.append(
                        PromptReference(
                            source_resource_id=source["resource_id"],
                            source_version_id=source["id"],
                            source_name=source["content"].get("name"),
                            version_label=source["version_label"],
                            status=display_status(source["state"]),
                            target_version_id=ref["target_version_id"],
                            target_version_label=ids[ref["target_version_id"]]["version_label"],
                            resource_type_label="智能体"
                            if source["resource_type"] == "agent"
                            else "配置资源",
                        )
                    )
        return result

    async def compare(
        self, context: AuthContext, version_id: str, other_id: str
    ) -> PromptCompareView:
        current, previous = (
            await self.version_detail(context, version_id),
            await self.version_detail(context, other_id),
        )
        if current.version.resource_id != previous.version.resource_id:
            raise ServiceError("PROMPT_COMPARE_INVALID", "只能对比同一提示词的版本", 422)
        return PromptCompareView(
            differences=compare_content(
                PromptContent.model_validate(previous.version.content),
                PromptContent.model_validate(current.version.content),
            ),
            references=await self.references(context, current.version.resource_id),
        )

    async def releases(self, context: AuthContext, prompt_id: str) -> list[PromptReleaseView]:
        await self.authorization.require(context, "version:read", prompt_id)
        async with self.engine.connect() as connection:
            rows = await repository("release_mappings", context.scope).find(
                connection, resource_type="prompt", resource_id=prompt_id
            )
            result = []
            for row in rows:
                version = await repository("resource_versions", context.scope).get(
                    connection, row["version_id"]
                )
                if version:
                    result.append(
                        PromptReleaseView(
                            environment=row["environment"],
                            environment_label=ENVIRONMENTS[row["environment"]],
                            version_id=row["version_id"],
                            version_label=version["version_label"],
                            revision=row["revision"],
                            published_at=row["updated_at"],
                        )
                    )
        return result

    async def release(
        self, context: AuthContext, prompt_id: str, body: PromptReleaseRequest
    ) -> PromptReleaseView:
        await self.authorization.require(context, "release:publish", prompt_id)
        version = await self.read_version(context, body.version_id)
        if version.resource_id != prompt_id:
            raise ServiceError("NOT_FOUND", "提示词版本不存在", 404)
        if body.operation == "rollback" and version.state != "PUBLISHED":
            raise ServiceError("VERSION_UNAVAILABLE", "只能回滚到已有发布版本")
        async with self.engine.connect() as connection:
            row = await repository("resource_versions", context.scope).get(
                connection, body.version_id
            )
        if row is None or row["revision"] != body.revision:
            raise ServiceError("REVISION_CONFLICT", "版本已变更，请刷新后检查差异")
        if version.state == "DRAFT":
            await self.versions.freeze(context, version.version_id, body.revision)
        await self.versions.release(
            context,
            body.version_id,
            body.expected_mapping_revision,
            f"{'回滚' if body.operation == 'rollback' else '发布'}：{body.note}",
        )
        return (await self.releases(context, prompt_id))[0]

    async def retire(
        self, context: AuthContext, version_id: str, revision: int
    ) -> PromptVersionView:
        version = await self.read_version(context, version_id)
        await self.authorization.require(context, "prompt:manage", version.resource_id)
        await self.authorization.require(context, "release:publish", version.resource_id)
        scope, audit_id = context.scope, new_id("audit")
        async with transaction(
            self.engine, scope, VersionService.keys(scope, version_id, audit_id)
        ) as uow:
            await DeletionGuard(scope).check(uow, [ContentRef("version", version_id)])
            refs = await repository("resource_references", scope).find(
                uow.connection, target_version_id=version_id
            )
            for ref in refs:
                source = await repository("resource_versions", scope).get(
                    uow.connection, ref["source_version_id"]
                )
                if source and source["state"] == "PUBLISHED":
                    raise ServiceError(
                        "VERSION_REFERENCED", "版本仍被已发布资源使用，请先查看引用并处理依赖"
                    )
            # 配置属于渠道，退役必须检查全部环境映射；这里只扩大映射范围，不读取其他主体内容。
            from sqlalchemy import select

            from creativity_service.core.database.tables import metadata

            mappings = metadata.tables["release_mappings"]
            mapped = await uow.connection.scalar(
                select(mappings.c.id)
                .where(
                    mappings.c.channel_id == scope.channel_id, mappings.c.version_id == version_id
                )
                .limit(1)
            )
            if mapped:
                raise ServiceError("VERSION_REFERENCED", "版本仍被环境发布映射使用，请先切换映射")
            await repository("resource_versions", scope).change(
                uow, version_id, revision, {"state": "RETIRED"}
            )
            await append_audit(
                uow,
                context,
                audit_id,
                "prompt.version.retire",
                "version",
                version_id,
                {"state": "RETIRED"},
            )
        return await self.version_detail(context, version_id)

    async def import_content(
        self, context: AuthContext, body: PromptImportRequest
    ) -> PromptVersionView:
        try:
            content = (
                PromptPortable.model_validate_json(body.data).content
                if body.format == "json"
                else import_text(body.data)
            )
        except ValidationError as exc:
            raise ServiceError(
                "PROMPT_IMPORT_INVALID", "导入文件不符合提示词配置格式", 422
            ) from exc
        if not body.version_label.strip():
            raise ServiceError("VERSION_LABEL_INVALID", "版本名称不能为空", 422)
        await self.validate_edit(context, "new", content)
        await self.authorization.require(context, "version:edit", "new")
        resource = await self.create(context, body.resource)
        return await self.create_draft(
            context,
            resource.prompt_id,
            PromptDraftCreate(version_label=body.version_label, content=content),
        )

    async def export_content(self, context: AuthContext, version_id: str, format: str) -> Artifact:
        version = await self.read_version(context, version_id)
        await self.authorization.require(context, "data:export", version.resource_id)
        if self.artifacts is None:
            raise unavailable("受控产物服务")
        content = redacted_content(PromptContent.model_validate(version.content))
        portable = PromptPortable(content=content)
        # 只导出定义白名单，绝不读取样例、运行原文、凭据、资源授权或渠道绑定。
        if format == "json":
            data, mime = canonical_json(portable.model_dump(mode="json")), "application/json"
        elif format == "text":
            data = export_text(content).encode("utf-8")
            mime = "text/plain"
        else:
            raise ServiceError("PROMPT_EXPORT_INVALID", "不支持此导出格式", 422)
        return await self.artifacts.upload(
            context,
            f"提示词-{version.version_label}.{'json' if format == 'json' else 'txt'}",
            mime,
            data,
            [ContentRef("version", version_id)],
        )

    async def route_options(self, context: AuthContext) -> list["PromptRouteOption"]:
        async with self.engine.connect() as connection:
            rows = await repository("resource_versions", context.scope).find(
                connection, resource_type="model_route", state="PUBLISHED"
            )
        result = []
        for row in rows:
            try:
                name = await self.require_model_route(context, row["id"])
            except ServiceError as exc:
                if exc.status in {403, 404}:
                    continue
                raise
            result.append(
                PromptRouteOption(
                    version_id=row["id"],
                    name=name,
                    version_label=row["version_label"],
                )
            )
        return result

    async def require_model_route(self, context: AuthContext, version_id: str) -> str | None:
        async with self.engine.connect() as connection:
            row = await repository("resource_versions", context.scope).get(connection, version_id)
        if row is None or row["resource_type"] != "model_route" or row["state"] != "PUBLISHED":
            raise ServiceError("MODEL_ROUTE_UNAVAILABLE", "请选择同渠道的已发布模型路由", 422)
        authorization = self.authorization.authorization
        if isinstance(authorization, IamAuthorization):
            decision = await authorization.check(
                context, "version:read", "model_route", row["resource_id"]
            )
            if not decision.allowed:
                raise ServiceError("FORBIDDEN", "无权使用此模型路由", 403)
        else:
            await authorization.require(context, "version:read", version_id)
        if isinstance(authorization, IamAuthorization) and authorization.resources:
            state = await authorization.resources.read_current(
                context, "model_route", row["resource_id"]
            )
            return state.name if state else None
        name = row["content"].get("name")
        return name if isinstance(name, str) else None
