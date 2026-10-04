"""样本修改创建新版本，来源校验与派生登记共用删除图事务。"""

from datetime import UTC
from typing import Any

from sqlalchemy import select

from creativity_service.core.context import AuthContext, Scope
from creativity_service.core.contracts import VisibleAction
from creativity_service.core.database import Repository, UnitOfWork, transaction
from creativity_service.core.deletion import ContentRef, DeletionGuard
from creativity_service.core.observability.audit import append_audit
from creativity_service.core.primitives import ServiceError, digest, new_id, utcnow
from creativity_service.modules.agents.access import (
    LockedAuthorization,
    locked_policy,
    locked_require,
)
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
from creativity_service.modules.iam.reading import require_action, resource_state, visible_actions


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
        permissions = await self.authorization.allowed_actions(context, "evaluation", identifier)
        return visible_actions(permissions, values)

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
        policy = await self.authorization.read_policy(context)
        require_action(policy.actions("evaluation", "scope"), "evaluation:read")
        async with self.engine.connect() as connection:
            rows = await repository("evaluation_datasets", context.scope).find(connection)
        items = [
            self.dataset_view(r, [], frozenset())
            for r in rows
            if "evaluation:read"
            in policy.actions("evaluation", r["id"], resource_state(context, "evaluation", r))
        ]
        return DatasetList(
            items=items,
            actions=visible_actions(
                policy.actions("evaluation", "new"), [("create", "新建样本集", "evaluation:manage")]
            ),
        )

    @staticmethod
    def dataset_view(
        row: dict[str, Any], versions: list[DatasetVersionView], permissions: frozenset[str]
    ) -> DatasetView:
        return DatasetView(
            dataset_id=row["id"],
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
            versions=versions,
            actions=visible_actions(
                permissions,
                [
                    ("version", "新建样本版本", "evaluation:manage"),
                    ("import", "导入样本", "evaluation:manage"),
                    ("review", "人工标注", "evaluation:review"),
                ],
            ),
        )

    async def dataset(
        self, context: AuthContext, identifier: str, *, versions: bool = True
    ) -> DatasetView:
        policy = await self.authorization.read_policy(context)
        async with transaction(self.engine, context.scope, keys(context)) as uow:
            row = await required(uow.connection, context.scope, "evaluation_datasets", identifier)
            permissions = policy.actions(
                "evaluation", identifier, resource_state(context, "evaluation", row)
            )
            require_action(permissions, "evaluation:read")
            content = "evaluation:content" in permissions
            found = (
                await repository("evaluation_dataset_versions", context.scope).find(
                    uow.connection, dataset_id=identifier
                )
                if versions
                else []
            )
            cases_by_id = await repository("evaluation_cases", context.scope).get_many(
                uow.connection, [i for v in found for i in v["case_ids"]]
            )
            validity = await self.valid_cases(uow, context, list(cases_by_id.values()))
            views = []
            for version in sorted(found, key=lambda v: v["created_at"]):
                cases = []
                for case_id in version["case_ids"]:
                    case = cases_by_id.get(case_id)
                    if case is None:
                        raise ServiceError("NOT_FOUND", "评测样本不存在", 404)
                    valid = validity[case_id]
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
        return self.dataset_view(row, views, permissions)

    @staticmethod
    async def valid_cases(
        uow: UnitOfWork, context: AuthContext, cases: list[dict[str, Any]]
    ) -> dict[str, bool]:
        from creativity_service.modules.runs.tables import metadata as runs

        eligible = [c for c in cases if not c["invalidated"] and c["payload"] is not None]
        identifiers = {
            s["resource_id"]
            for c in eligible
            for s in c["payload"].get("source_refs", [])
            if s["resource_type"] == "run"
        }
        table = runs.tables["runs"]
        source_rows = (
            [
                dict(r)
                for r in (
                    await uow.connection.execute(
                        select(table).where(
                            table.c.channel_id == context.scope.channel_id,
                            table.c.id.in_(identifiers),
                        )
                    )
                ).mappings()
            ]
            if identifiers
            else []
        )
        sources = {r["id"]: r for r in source_rows}
        if len(sources) != len(source_rows):
            raise ServiceError("STORAGE_INVARIANT_BROKEN", "评测来源标识重复", 503)
        if identifiers - sources.keys():
            raise ServiceError("NOT_FOUND", "评测来源不存在", 404)
        scopes = [Scope.model_validate({k: r[k] for k in Scope.model_fields}) for r in source_rows]
        if any(
            (s.environment, s.data_scope_id)
            != (context.scope.environment, context.scope.data_scope_id)
            for s in scopes
        ):
            raise ServiceError("SCOPE_MISMATCH", "样本来源不在当前数据域", 403)
        refs = [ContentRef("evaluation_case", c["id"]) for c in eligible]
        refs.extend(ContentRef("run", i) for i in identifiers)
        blocked = await DeletionGuard(context.scope).blocked_refs(
            uow, refs, scopes=[context.scope, *scopes]
        )
        policy = await locked_policy(uow, context) if identifiers else None
        result = {c["id"]: False for c in cases}
        for case in eligible:
            source_ids = {
                s["resource_id"]
                for s in case["payload"].get("source_refs", [])
                if s["resource_type"] == "run"
            }
            if ContentRef("evaluation_case", case["id"]) in blocked or any(
                ContentRef("run", i) in blocked for i in source_ids
            ):
                continue
            if policy:
                for identifier in source_ids:
                    scoped = context.model_copy(
                        update={
                            "scope": Scope.model_validate(
                                {k: sources[identifier][k] for k in Scope.model_fields}
                            )
                        }
                    )
                    policy.require(scoped, "run:content", "run", identifier)
            result[case["id"]] = True
        return result

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
        self,
        uow: UnitOfWork,
        context: AuthContext,
        cases: list[CaseInput],
        references: list[str],
        policy: LockedAuthorization,
    ) -> dict[str, dict[str, Any]]:
        """批量导入在同一写事务内复核所有来源；不逐样本重入完整鉴权流程。"""
        from creativity_service.storage import metadata

        refs = {(r.resource_type, r.resource_id) for case in cases for r in case.source_refs}
        refs.update(("version", identifier) for identifier in references)
        fixtures = {f.tool_version_id for case in cases for f in case.fixture}
        versions = await agent_repository("resource_versions", context.scope).get_many(
            uow.connection, fixtures | {i for kind, i in refs if kind == "version"}
        )
        tools = await agent_repository("tools", context.scope).get_many(
            uow.connection,
            [
                v["resource_id"]
                for identifier, v in versions.items()
                if identifier in fixtures and v["resource_type"] == "tool"
            ],
        )
        for identifier in fixtures:
            tool = versions.get(identifier)
            if not tool or tool["resource_type"] != "tool" or tool["resource_id"] not in tools:
                raise ServiceError("FIXTURE_TOOL_INVALID", "夹具工具不属于当前渠道", 422)
            policy.require(context, "run:create", "tool", tool["resource_id"])
        artifacts = await Repository(metadata.tables["artifacts"], context.scope).get_many(
            uow.connection, [i for kind, i in refs if kind == "artifact"]
        )
        run_ids = {i for kind, i in refs if kind == "run"}
        runs = metadata.tables["runs"]
        source_rows = (
            [
                dict(row)
                for row in (
                    await uow.connection.execute(
                        select(runs).where(
                            runs.c.channel_id == context.scope.channel_id, runs.c.id.in_(run_ids)
                        )
                    )
                ).mappings()
            ]
            if run_ids
            else []
        )
        source_runs = {row["id"]: row for row in source_rows}
        if len(source_runs) != len(source_rows):
            raise ServiceError("STORAGE_INVARIANT_BROKEN", "评测来源标识重复", 503)
        connection_ids = {
            v["resource_id"]
            if v["resource_type"] == "model_connection"
            else v["content"].get("connection_id")
            for v in versions.values()
            if v["resource_type"] in {"model", "model_connection"}
        } - {None}
        connections = await agent_repository("model_connections", context.scope).get_many(
            uow.connection, connection_ids
        )
        scopes = [context.scope]
        for case in cases:
            if case.human_label:
                policy.require(context, "evaluation:review", "evaluation", "scope")
        for kind, identifier in refs:
            row = {"run": source_runs, "version": versions, "artifact": artifacts}[kind].get(
                identifier
            )
            if row is None:
                raise ServiceError("NOT_FOUND", "评测来源不存在", 404)
            scoped = self.runs.row_context(context, row) if kind == "run" else context
            if kind == "run":
                scopes.append(scoped.scope)
            if kind == "artifact" and row["state"] in {"DELETED", "DELETING"}:
                raise ServiceError("RESOURCE_DISABLED", "资源已停用", 403)
            if kind == "version" and row["resource_type"] in {
                "model",
                "model_connection",
                "model_route",
            }:
                connection_id = (
                    row["resource_id"]
                    if row["resource_type"] == "model_connection"
                    else row["content"].get("connection_id")
                )
                if (
                    connection_id
                    and connection_id not in connections
                    or (
                        row["resource_type"] == "model_route"
                        and any(
                            model["scope"]["environment"] != context.scope.environment
                            for model in row["content"].get("models", [])
                        )
                    )
                ):
                    raise ServiceError("NOT_FOUND", "评测来源不在当前环境", 404)
                if row["state"] == "RETIRED":
                    raise ServiceError("RESOURCE_DISABLED", "资源已停用", 403)
            policy.require(
                scoped,
                {"run": "run:content", "version": "version:read", "artifact": "artifact:download"}[
                    kind
                ],
                kind,
                identifier,
            )
        if await DeletionGuard(context.scope).blocked_refs(
            uow, [ContentRef(kind, identifier) for kind, identifier in refs], scopes=scopes
        ):
            raise ServiceError("CONTENT_DELETED", "内容或来源已删除", 410)
        return versions

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
            policy = await locked_policy(uow, context)
            policy.require(context, action, "evaluation", dataset_id)
            versions = await self.check_sources(
                uow, context, body.cases, body.reference_versions, policy
            )
            independent = {
                identifier: await validate_independent(
                    uow, context.scope, case.model_dump(mode="json")
                )
                for identifier, _, case in prepared
            }
            if any(case.human_label for case in body.cases):
                policy.require(context, "evaluation:review", "evaluation", dataset_id)
            reference_digests = {}
            for reference in body.reference_versions:
                row = versions.get(reference)
                if not row or row["state"] != "PUBLISHED":
                    raise ServiceError("REFERENCE_INVALID", "参考资料必须固定已发布版本", 422)
                reference_digests[reference] = row["content_digest"]
            await repository("evaluation_datasets", context.scope).change(
                uow, dataset_id, body.revision, {"current_version_id": version_id}
            )
            fixture_values = {}
            case_values = {}
            for identifier, fixture_id, case in prepared:
                if fixture_id:
                    if any(f.observed_at > body.captured_at for f in case.fixture):
                        raise ServiceError("DATA_TIME_INVALID", "夹具观测时间晚于固定数据时间", 422)
                    fixture_values[fixture_id] = {
                        "payload": [f.model_dump(mode="json") for f in case.fixture],
                        "captured_at": body.captured_at,
                        "invalidated": False,
                    }
                case_values[identifier] = {
                    "dataset_id": dataset_id,
                    "case_key": case.case_key,
                    "title": case.title,
                    "payload": case.model_dump(mode="json"),
                    "fixture_id": fixture_id,
                    "previous_case_id": (replacements or {}).get(case.case_key),
                    "invalidated": False,
                }
            await repository("evaluation_fixtures", context.scope).add_many(uow, fixture_values)
            await repository("evaluation_cases", context.scope).add_many(uow, case_values)
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
            await DeletionGuard(context.scope).link_many(
                uow,
                [
                    (
                        link_id(source, derived),
                        source,
                        derived,
                        independent.get(derived.resource_id)
                        if source.resource_type == "run"
                        else None,
                    )
                    for source, derived in links
                ],
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
            found = await repository("evaluation_cases", context.scope).get_many(
                uow.connection, version["case_ids"]
            )
            if set(version["case_ids"]) - found.keys():
                raise ServiceError("NOT_FOUND", "评测样本不存在", 404)
            cases = [found[identifier] for identifier in version["case_ids"]]
            if not all((await self.valid_cases(uow, context, cases)).values()):
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
