"""样本修改创建新版本，来源校验与派生登记共用删除图事务。"""

from datetime import UTC
from typing import Any

from creativity_service.core.context import AuthContext, Scope
from creativity_service.core.contracts import VisibleAction
from creativity_service.core.database import UnitOfWork, transaction
from creativity_service.core.deletion import ContentRef, DeletionGuard
from creativity_service.core.observability.audit import append_audit
from creativity_service.core.primitives import ServiceError, digest, new_id, utcnow
from creativity_service.modules.agents.access import locked_require
from creativity_service.modules.agents.repositories import repository as agent_repository
from creativity_service.modules.evaluations.imports import preview
from creativity_service.modules.evaluations.independent import validate_independent
from creativity_service.modules.evaluations.judges import MISSING, field
from creativity_service.modules.evaluations.redaction import redact
from creativity_service.modules.evaluations.repositories import (
    keys,
    link_id,
    link_keys,
    repository,
    required,
)
from creativity_service.modules.evaluations.schemas import (
    CaseEdit,
    CaseInput,
    DatasetCreate,
    DatasetList,
    DatasetVersionInput,
    DatasetVersionView,
    DatasetView,
    EvaluationCaseView,
    ImportInput,
    ImportPreview,
    RunCaseInput,
)


class DatasetService:
    engine: Any
    authorization: Any
    runs: Any

    async def require(self, context: AuthContext, action: str, identifier: str = "scope") -> None:
        await self.authorization.boundary(context, action, "evaluation", identifier)

    async def allowed(self, context: AuthContext, action: str, identifier: str = "scope") -> bool:
        return bool(
            (await self.authorization.check(context, action, "evaluation", identifier)).allowed
        )

    async def actions(
        self, context: AuthContext, identifier: str, values: list[tuple[str, str, str]]
    ) -> list[VisibleAction]:
        return [
            VisibleAction(action_key=k, label=n)
            for k, n, action in values
            if await self.allowed(context, action, identifier)
        ]

    async def create_dataset(self, context: AuthContext, body: DatasetCreate) -> DatasetView:
        await self.require(context, "evaluation:manage", "new")
        identifier = new_id("dataset")
        async with transaction(
            self.engine, context.scope, keys(context, [("evaluation_datasets", identifier)])
        ) as uow:
            await locked_require(uow, context, "evaluation:manage", "evaluation", "new")
            await repository("evaluation_datasets", context.scope).add(
                uow, identifier, {**body.model_dump(), "current_version_id": None}
            )
        return await self.dataset(context, identifier)

    async def datasets(self, context: AuthContext) -> DatasetList:
        await self.require(context, "evaluation:read")
        async with self.engine.connect() as connection:
            rows = await repository("evaluation_datasets", context.scope).find(connection)
        items = [
            await self.dataset(context, r["id"], versions=False)
            for r in rows
            if await self.allowed(context, "evaluation:read", r["id"])
        ]
        return DatasetList(
            items=items,
            actions=await self.actions(
                context, "new", [("create", "新建样本集", "evaluation:manage")]
            ),
        )

    async def dataset(
        self, context: AuthContext, identifier: str, *, versions: bool = True
    ) -> DatasetView:
        await self.require(context, "evaluation:read", identifier)
        content = await self.allowed(context, "evaluation:content", identifier)
        async with transaction(self.engine, context.scope, keys(context)) as uow:
            row = await required(uow.connection, context.scope, "evaluation_datasets", identifier)
            found = (
                await repository("evaluation_dataset_versions", context.scope).find(
                    uow.connection, dataset_id=identifier
                )
                if versions
                else []
            )
            views = []
            for version in sorted(found, key=lambda v: v["created_at"]):
                cases = []
                for case_id in version["case_ids"]:
                    case = await required(
                        uow.connection, context.scope, "evaluation_cases", case_id
                    )
                    valid = await self.valid_case(uow, context, case)
                    cases.append(
                        EvaluationCaseView(
                            case_id=case_id,
                            case_key=case["case_key"] if valid else case["id"],
                            title=case["title"] if valid else "来源已删除的样本",
                            valid=valid,
                            payload=CaseInput.model_validate(case["payload"])
                            if content and valid
                            else None,
                        )
                    )
                views.append(
                    DatasetVersionView(
                        version_id=version["id"],
                        version_label=version["version_label"],
                        content_digest=version["content_digest"],
                        captured_at=version["captured_at"],
                        reference_versions=version["reference_versions"],
                        cases=cases,
                    )
                )
        return DatasetView(
            dataset_id=identifier,
            **{
                k: row[k]
                for k in (
                    "name",
                    "scenario",
                    "owner",
                    "applicability",
                    "revision",
                    "current_version_id",
                )
            },
            versions=views,
            actions=await self.actions(
                context,
                identifier,
                [
                    ("version", "新建样本版本", "evaluation:manage"),
                    ("import", "导入样本", "evaluation:manage"),
                    ("review", "人工标注", "evaluation:review"),
                ],
            ),
        )

    @staticmethod
    async def valid_case(uow: UnitOfWork, context: AuthContext, case: dict[str, Any]) -> bool:
        if case["invalidated"] or case["payload"] is None:
            return False
        try:
            await DeletionGuard(context.scope).check(
                uow, [ContentRef("evaluation_case", case["id"])]
            )
            await DatasetService.source_guard(uow, context, case["payload"])
            return True
        except ServiceError as exc:
            if exc.code != "CONTENT_DELETED":
                raise
            return False

    @staticmethod
    async def source_guard(uow: UnitOfWork, context: AuthContext, payload: dict[str, Any]) -> None:
        from creativity_service.modules.runs.repositories import required as run_required

        for source in payload.get("source_refs", []):
            if source["resource_type"] != "run":
                continue
            row = await run_required(
                uow.connection, "runs", context.scope.channel_id, id=source["resource_id"]
            )
            scope = Scope.model_validate({k: row[k] for k in Scope.model_fields})
            if (scope.environment, scope.data_scope_id) != (
                context.scope.environment,
                context.scope.data_scope_id,
            ):
                raise ServiceError("SCOPE_MISMATCH", "样本来源不在当前数据域", 403)
            # 沿服务端已登记的来源主体复核删除；复用同一连接与渠道图锁，不开启嵌套事务。
            source_uow = UnitOfWork(uow.connection, scope, uow.keys)
            await DeletionGuard(scope).check(source_uow, [ContentRef("run", source["resource_id"])])
            await locked_require(
                source_uow,
                context.model_copy(update={"scope": scope}),
                "run:content",
                "run",
                source["resource_id"],
            )

    async def check_sources(
        self, context: AuthContext, cases: list[CaseInput], references: list[str]
    ) -> None:
        for case in cases:
            if case.human_label:
                await self.require(context, "evaluation:review")
            for fixture in case.fixture:
                async with self.engine.connect() as connection:
                    tool = await agent_repository("resource_versions", context.scope).get(
                        connection, fixture.tool_version_id
                    )
                if not tool or tool["resource_type"] != "tool":
                    raise ServiceError("FIXTURE_TOOL_INVALID", "夹具工具不属于当前渠道", 422)
                await self.authorization.boundary(
                    context, "run:create", "tool", tool["resource_id"]
                )
            for ref in case.source_refs:
                if ref.resource_type == "run":
                    source_context = await self.runs.access_context(context, ref.resource_id)
                    await self.runs.authorization.require(
                        source_context, "run:content", ref.resource_id
                    )
                else:
                    action = (
                        "artifact:download" if ref.resource_type == "artifact" else "version:read"
                    )
                    await self.authorization.boundary(
                        context, action, ref.resource_type, ref.resource_id
                    )
        for identifier in references:
            await self.authorization.boundary(context, "version:read", "version", identifier)

    async def create_version(
        self,
        context: AuthContext,
        dataset_id: str,
        body: DatasetVersionInput,
        *,
        replacements: dict[str, str] | None = None,
        action: str = "evaluation:manage",
    ) -> DatasetView:
        await self.require(context, action, dataset_id)
        if len({c.case_key for c in body.cases}) != len(body.cases):
            raise ServiceError("CASE_DUPLICATE", "样本定位键重复", 422)
        if body.captured_at > utcnow():
            raise ServiceError("DATA_TIME_INVALID", "固定数据时间不能晚于当前时间", 422)
        for case in body.cases:
            fixture_keys = [(f.tool_version_id, digest(f.arguments)) for f in case.fixture]
            if len(set(fixture_keys)) != len(fixture_keys):
                raise ServiceError("FIXTURE_AMBIGUOUS", "同一样本的工具版本和参数夹具不能重复", 422)
        await self.check_sources(context, body.cases, body.reference_versions)
        version_id = new_id("dataset_version")
        audit_id = new_id("audit")
        prepared = [
            (new_id("case"), new_id("fixture") if c.fixture else None, c) for c in body.cases
        ]
        links: list[tuple[ContentRef, ContentRef]] = []
        records = [
            ("audit_events", audit_id),
            ("evaluation_datasets", dataset_id),
            ("evaluation_dataset_versions", version_id),
        ]
        for identifier, fixture_id, case in prepared:
            records.append(("evaluation_cases", identifier))
            derived = ContentRef("evaluation_case", identifier)
            links.extend(
                (ContentRef(s.resource_type, s.resource_id), derived) for s in case.source_refs
            )
            if replacements and case.case_key in replacements:
                links.append((ContentRef("evaluation_case", replacements[case.case_key]), derived))
            if fixture_id:
                records.append(("evaluation_fixtures", fixture_id))
                links.extend(
                    (
                        ContentRef("version", f.tool_version_id),
                        ContentRef("evaluation_fixture", fixture_id),
                    )
                    for f in case.fixture
                )
                links.append((ContentRef("evaluation_fixture", fixture_id), derived))
                links.extend(
                    (
                        ContentRef(s.resource_type, s.resource_id),
                        ContentRef("evaluation_fixture", fixture_id),
                    )
                    for s in case.source_refs
                )
            links.append((derived, ContentRef("evaluation_dataset_version", version_id)))
        links.extend(
            (ContentRef("version", v), ContentRef("evaluation_dataset_version", version_id))
            for v in body.reference_versions
        )
        async with transaction(
            self.engine, context.scope, keys(context, records) + link_keys(context, links)
        ) as uow:
            independent = {
                identifier: await validate_independent(
                    uow, context.scope, case.model_dump(mode="json")
                )
                for identifier, _, case in prepared
            }
            await locked_require(uow, context, action, "evaluation", dataset_id)
            for case in body.cases:
                await self.source_guard(uow, context, case.model_dump(mode="json"))
                if case.human_label:
                    await locked_require(
                        uow, context, "evaluation:review", "evaluation", dataset_id
                    )
                for ref in case.source_refs:
                    await locked_require(
                        uow,
                        context,
                        {
                            "run": "run:content",
                            "version": "version:read",
                            "artifact": "artifact:download",
                        }[ref.resource_type],
                        ref.resource_type,
                        ref.resource_id,
                    )
            reference_digests = {}
            for reference in body.reference_versions:
                row = await agent_repository("resource_versions", context.scope).get(
                    uow.connection, reference
                )
                if not row or row["state"] != "PUBLISHED":
                    raise ServiceError("REFERENCE_INVALID", "参考资料必须固定已发布版本", 422)
                reference_digests[reference] = row["content_digest"]
            await repository("evaluation_datasets", context.scope).change(
                uow, dataset_id, body.revision, {"current_version_id": version_id}
            )
            for identifier, fixture_id, case in prepared:
                if fixture_id:
                    if any(f.observed_at > body.captured_at for f in case.fixture):
                        raise ServiceError("DATA_TIME_INVALID", "夹具观测时间晚于固定数据时间", 422)
                    await repository("evaluation_fixtures", context.scope).add(
                        uow,
                        fixture_id,
                        {
                            "payload": [f.model_dump(mode="json") for f in case.fixture],
                            "captured_at": body.captured_at,
                            "invalidated": False,
                        },
                    )
                await repository("evaluation_cases", context.scope).add(
                    uow,
                    identifier,
                    {
                        "dataset_id": dataset_id,
                        "case_key": case.case_key,
                        "title": case.title,
                        "payload": case.model_dump(mode="json"),
                        "fixture_id": fixture_id,
                        "previous_case_id": (replacements or {}).get(case.case_key),
                        "invalidated": False,
                    },
                )
            await repository("evaluation_dataset_versions", context.scope).add(
                uow,
                version_id,
                {
                    "dataset_id": dataset_id,
                    "version_label": body.version_label,
                    "case_ids": [i for i, _, _ in prepared],
                    "content_digest": digest(
                        {
                            "cases": [c.model_dump(mode="json") for c in body.cases],
                            "reference_versions": body.reference_versions,
                            "captured_at": body.captured_at.astimezone(UTC).isoformat(),
                            "reference_digests": reference_digests,
                        }
                    ),
                    "reference_versions": body.reference_versions,
                    "reference_digests": reference_digests,
                    "captured_at": body.captured_at,
                },
            )
            await append_audit(
                uow,
                context,
                audit_id,
                "evaluation.dataset_version",
                "evaluation",
                dataset_id,
                {
                    "version_id": version_id,
                    "revision": body.revision,
                },
            )
            for source, derived in links:
                await DeletionGuard(context.scope).link(
                    uow,
                    link_id(source, derived),
                    source,
                    derived,
                    independent.get(derived.resource_id) if source.resource_type == "run" else None,
                )
        return await self.dataset(context, dataset_id)

    async def current_cases(
        self, context: AuthContext, dataset_id: str
    ) -> tuple[dict[str, Any], list[dict[str, Any]]]:
        await self.require(context, "evaluation:content", dataset_id)
        async with transaction(self.engine, context.scope, keys(context)) as uow:
            dataset = await required(
                uow.connection, context.scope, "evaluation_datasets", dataset_id
            )
            version = await required(
                uow.connection,
                context.scope,
                "evaluation_dataset_versions",
                dataset["current_version_id"],
            )
            await DeletionGuard(context.scope).check(
                uow, [ContentRef("evaluation_dataset_version", version["id"])]
            )
            cases = [
                await required(uow.connection, context.scope, "evaluation_cases", i)
                for i in version["case_ids"]
            ]
            for case in cases:
                if not await self.valid_case(uow, context, case):
                    raise ServiceError("CONTENT_DELETED", "当前版本包含已删除来源，不能复制", 410)
        return version, cases

    async def edit_case(self, context: AuthContext, case_id: str, body: CaseEdit) -> DatasetView:
        async with self.engine.connect() as connection:
            case = await required(connection, context.scope, "evaluation_cases", case_id)
        version, cases = await self.current_cases(context, case["dataset_id"])
        if case_id not in version["case_ids"] or body.case.case_key != case["case_key"]:
            raise ServiceError("REVISION_CONFLICT", "请在最新样本版本上修改标注", 409)
        before = CaseInput.model_validate(case["payload"])
        if body.case.source_refs != before.source_refs:
            raise ServiceError("SOURCE_IMMUTABLE", "样本修订不能移除或替换已登记来源", 422)
        label_only = before.model_dump(
            exclude={"human_label", "labels", "label_source"}
        ) == body.case.model_dump(exclude={"human_label", "labels", "label_source"})
        return await self.create_version(
            context,
            case["dataset_id"],
            DatasetVersionInput(
                revision=body.revision,
                version_label=body.version_label,
                captured_at=version["captured_at"],
                reference_versions=version["reference_versions"],
                cases=[
                    body.case if r["id"] == case_id else CaseInput.model_validate(r["payload"])
                    for r in cases
                ],
            ),
            replacements={r["case_key"]: r["id"] for r in cases},
            action="evaluation:review" if label_only else "evaluation:manage",
        )

    async def import_cases(
        self, context: AuthContext, dataset_id: str, body: ImportInput
    ) -> ImportPreview:
        await self.require(context, "evaluation:manage", dataset_id)
        result = preview(body)
        if not body.commit:
            return result
        if body.preview_digest != result.preview_digest or result.error_count:
            raise ServiceError("IMPORT_INVALID", "请修正全部错误行并重新预览后提交", 422)
        value = await self.create_version(
            context,
            dataset_id,
            DatasetVersionInput(
                revision=body.revision,
                version_label=body.version_label,
                captured_at=body.captured_at,
                cases=[r.case for r in result.rows if r.case is not None],
            ),
        )
        return result.model_copy(update={"version_id": value.current_version_id})

    async def from_run(
        self, context: AuthContext, dataset_id: str, body: RunCaseInput
    ) -> DatasetView:
        await self.require(context, "evaluation:manage", dataset_id)
        await self.require(context, "evaluation:review", dataset_id)
        if body.review.decision != "approved":
            raise ServiceError("REVIEW_REQUIRED", "反馈样本需要人工审阅通过", 422)
        detail = await self.runs.detail(context, body.run_id)
        source = detail.get("input")
        if source is None:
            raise ServiceError("FORBIDDEN", "无权读取来源原文", 403)
        # 只复制经审阅的输入字段，禁止全量透传日志、提示词或模型隐藏内容。
        values: dict[str, Any] = {}
        for path in body.input_paths:
            value = field(source, path)
            if value is MISSING:
                raise ServiceError("SOURCE_FIELD_MISSING", "选取的来源字段不存在", 422)
            target = values
            parts = path.split(".")
            for part in parts[:-1]:
                target = target.setdefault(part, {})
            target[parts[-1]] = value
        case = CaseInput(
            case_key=body.case_key,
            title=body.title,
            input=redact(values),
            assertions=body.assertions,
            label_source=body.label_source,
            human_label=body.review,
            source_refs=[{"resource_type": "run", "resource_id": body.run_id}],
        )
        async with self.engine.connect() as connection:
            dataset = await required(connection, context.scope, "evaluation_datasets", dataset_id)
        old_cases = []
        references = []
        if dataset["current_version_id"]:
            version, rows = await self.current_cases(context, dataset_id)
            old_cases = [CaseInput.model_validate(r["payload"]) for r in rows]
            references = version["reference_versions"]
        return await self.create_version(
            context,
            dataset_id,
            DatasetVersionInput(
                revision=body.revision,
                version_label=body.version_label,
                captured_at=utcnow(),
                reference_versions=references,
                cases=[*old_cases, case],
            ),
        )
