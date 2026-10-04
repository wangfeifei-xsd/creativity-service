"""内容冻结、环境映射切换与可组合的执行快照入口。"""

from typing import Any, Literal, Protocol

from jsonschema import Draft202012Validator
from sqlalchemy.ext.asyncio import AsyncEngine

from creativity_service.core.context import AuthContext, Authorization, DenyAuthorization, Scope
from creativity_service.core.contracts import ReleaseSnapshot, ResourceVersion
from creativity_service.core.database import (
    Repository,
    UnitOfWork,
    assert_external_io_allowed,
    transaction,
)
from creativity_service.core.database.tables import metadata
from creativity_service.core.deletion import ContentRef, DeletionGuard, content_key
from creativity_service.core.locking import ResourceKey, record_key
from creativity_service.core.observability.audit import append_audit
from creativity_service.core.primitives import ServiceError, digest, new_id, unavailable, utcnow


class VersionValidator(Protocol):
    async def validate(
        self,
        uow: UnitOfWork,
        context: AuthContext,
        version: ResourceVersion,
        operation: Literal["freeze", "release"],
    ) -> None: ...


def version_view(row: dict[str, Any]) -> ResourceVersion:
    return ResourceVersion(
        channel_id=row["channel_id"],
        resource_type=row["resource_type"],
        resource_id=row["resource_id"],
        version_id=row["id"],
        version_label=row["version_label"],
        state=row["state"],
        draft_revision=row["revision"] if row["state"] == "DRAFT" else None,
        content=row["content"],
        content_digest=row["content_digest"],
        dependency_version_ids=tuple(row["dependencies"]),
        dependencies_digest=row["dependencies_digest"],
        output_schema=row["output_schema"],
    )


def validate_schema(schema: dict[str, Any]) -> None:
    Draft202012Validator.check_schema(schema)

    def visit(value: Any) -> None:
        if isinstance(value, dict):
            if any(
                key in {"$ref", "$dynamicRef"}
                and (not isinstance(item, str) or not item.startswith("#"))
                for key, item in value.items()
            ):
                raise ServiceError("SCHEMA_REFERENCE_FORBIDDEN", "输出结构不能引用外部地址", 422)
            for item in value.values():
                visit(item)
        elif isinstance(value, list):
            for item in value:
                visit(item)

    visit(schema)


class VersionService:
    def __init__(
        self,
        engine: AsyncEngine,
        authorization: Authorization | None = None,
        validator: VersionValidator | None = None,
    ) -> None:
        self.engine = engine
        self.authorization = authorization or DenyAuthorization()
        self.validator = validator

    @staticmethod
    def keys(scope: Scope, version_id: str, audit_id: str) -> list[ResourceKey]:
        return [
            content_key(scope),
            record_key(scope.channel_id, "resource_versions", version_id),
            record_key(scope.channel_id, "audit_events", audit_id),
        ]

    async def create_draft(
        self,
        context: AuthContext,
        resource_type: str,
        resource_id: str,
        label: str,
        content: dict[str, Any],
        dependencies: list[str],
        output_schema: dict[str, Any],
    ) -> ResourceVersion:
        assert_external_io_allowed()
        await self.authorization.require(context, "version:edit", resource_id)
        validate_schema(output_schema)
        version_id, audit_id = new_id("version"), new_id("audit")
        scope, repo = context.scope, Repository(metadata.tables["resource_versions"], context.scope)
        keys = self.keys(scope, version_id, audit_id) + [
            ResourceKey(scope.channel_id, "version-label", (resource_type, resource_id, label))
        ]
        source_id = digest([version_id, "resource"])
        keys.append(record_key(scope.channel_id, "source_links", source_id))
        async with transaction(self.engine, scope, keys) as uow:
            await DeletionGuard(scope).check(uow, [ContentRef(resource_type, resource_id)])
            if await repo.find(
                uow.connection,
                resource_type=resource_type,
                resource_id=resource_id,
                version_label=label,
            ):
                raise ServiceError("VERSION_LABEL_CONFLICT", "版本名称已存在")
            row = await repo.add(
                uow,
                version_id,
                {
                    "resource_type": resource_type,
                    "resource_id": resource_id,
                    "version_label": label,
                    "state": "DRAFT",
                    "content": content,
                    "content_digest": digest({"content": content, "output_schema": output_schema}),
                    "dependencies": sorted(set(dependencies)),
                    "dependencies_digest": digest(sorted(set(dependencies))),
                    "output_schema": output_schema,
                    "created_by": context.principal_id,
                },
            )
            await DeletionGuard(scope).link(
                uow,
                source_id,
                ContentRef(resource_type, resource_id),
                ContentRef("version", version_id),
            )
            await append_audit(
                uow,
                context,
                audit_id,
                "version.create",
                resource_type,
                resource_id,
                {"version_id": version_id},
            )
        return version_view(row)

    async def edit_draft(
        self,
        context: AuthContext,
        version_id: str,
        revision: int,
        content: dict[str, Any],
        dependencies: list[str],
        output_schema: dict[str, Any],
    ) -> ResourceVersion:
        assert_external_io_allowed()
        await self.authorization.require(context, "version:edit", version_id)
        validate_schema(output_schema)
        scope, audit_id = context.scope, new_id("audit")
        repo = Repository(metadata.tables["resource_versions"], scope)
        async with transaction(self.engine, scope, self.keys(scope, version_id, audit_id)) as uow:
            await DeletionGuard(scope).check(uow, [ContentRef("version", version_id)])
            row = await repo.get(uow.connection, version_id)
            if row is None:
                raise ServiceError("NOT_FOUND", "版本不存在", 404)
            if row["state"] != "DRAFT":
                raise ServiceError("VERSION_FROZEN", "发布版本不能修改")
            row = await repo.change(
                uow,
                version_id,
                revision,
                {
                    "content": content,
                    "content_digest": digest({"content": content, "output_schema": output_schema}),
                    "dependencies": sorted(set(dependencies)),
                    "dependencies_digest": digest(sorted(set(dependencies))),
                    "output_schema": output_schema,
                },
            )
            await append_audit(
                uow,
                context,
                audit_id,
                "version.edit",
                "version",
                version_id,
                {"revision": row["revision"]},
            )
        return version_view(row)

    async def freeze(self, context: AuthContext, version_id: str, revision: int) -> ResourceVersion:
        assert_external_io_allowed()
        await self.authorization.require(context, "version:freeze", version_id)
        if self.validator is None:
            raise unavailable("版本依赖与发布校验服务")
        scope, audit_id = context.scope, new_id("audit")
        repo = Repository(metadata.tables["resource_versions"], scope)
        async with self.engine.connect() as connection:
            initial = await repo.get(connection, version_id)
        if initial is None:
            raise ServiceError("NOT_FOUND", "版本不存在", 404)
        dependencies = initial["dependencies"]
        references = {dep: digest([version_id, dep]) for dep in dependencies}
        keys = self.keys(scope, version_id, audit_id)
        keys += [record_key(scope.channel_id, "resource_versions", dep) for dep in dependencies]
        keys += [
            record_key(scope.channel_id, "resource_references", ref) for ref in references.values()
        ]
        async with transaction(self.engine, scope, keys) as uow:
            await DeletionGuard(scope).check(
                uow, [ContentRef("version", item) for item in [version_id, *dependencies]]
            )
            row = await repo.get(uow.connection, version_id)
            if row is None or row["revision"] != revision or row["dependencies"] != dependencies:
                raise ServiceError("REVISION_CONFLICT", "草稿已变更，请重新检查")
            if row["state"] != "DRAFT":
                raise ServiceError("VERSION_FROZEN", "版本已经冻结")
            resolved = []
            for dep in dependencies:
                target = await repo.get(uow.connection, dep)
                if target is None or target["state"] != "PUBLISHED":
                    raise ServiceError("DEPENDENCY_INVALID", "依赖须为同渠道已发布版本")
                resolved.append(
                    {
                        "version_id": dep,
                        "content_digest": target["content_digest"],
                        "dependencies_digest": target["dependencies_digest"],
                    }
                )
                await Repository(metadata.tables["resource_references"], scope).add(
                    uow,
                    references[dep],
                    {
                        "source_version_id": version_id,
                        "target_version_id": dep,
                        "target_resource_type": target["resource_type"],
                    },
                )
            await self.validator.validate(uow, context, version_view(row), "freeze")
            row = await repo.change(
                uow,
                version_id,
                revision,
                {
                    "state": "PUBLISHED",
                    "dependencies_digest": digest(resolved),
                },
            )
            await append_audit(
                uow,
                context,
                audit_id,
                "version.freeze",
                "version",
                version_id,
                {"content_digest": row["content_digest"]},
            )
        return version_view(row)

    async def release(
        self, context: AuthContext, version_id: str, expected_revision: int | None, note: str
    ) -> dict[str, Any]:
        assert_external_io_allowed()
        await self.authorization.require(context, "release:publish", version_id)
        if self.validator is None:
            raise unavailable("当前依赖与发布门禁服务")
        scope = context.scope
        versions = Repository(metadata.tables["resource_versions"], scope)
        async with self.engine.connect() as connection:
            initial = await versions.get(connection, version_id)
        if initial is None:
            raise ServiceError("NOT_FOUND", "版本不存在", 404)
        mapping_id = digest(
            [scope.channel_id, scope.environment, initial["resource_type"], initial["resource_id"]]
        )
        audit_id = new_id("audit")
        repo = Repository(metadata.tables["release_mappings"], scope)
        keys = self.keys(scope, version_id, audit_id) + [
            record_key(scope.channel_id, "release_mappings", mapping_id)
        ]
        async with transaction(self.engine, scope, keys) as uow:
            await DeletionGuard(scope).check(uow, [ContentRef("version", version_id)])
            row = await versions.get(uow.connection, version_id)
            if row is None or row["state"] != "PUBLISHED":
                raise ServiceError("VERSION_UNAVAILABLE", "只能发布已冻结版本")
            await self.validator.validate(uow, context, version_view(row), "release")
            previous = await repo.get(uow.connection, mapping_id)
            if (previous["revision"] if previous else None) != expected_revision:
                raise ServiceError("REVISION_CONFLICT", "环境发布映射已变更")
            values = {
                "resource_type": row["resource_type"],
                "resource_id": row["resource_id"],
                "version_id": version_id,
                "published_by": context.principal_id,
                "release_note": note,
            }
            if previous:
                result = await repo.change(uow, mapping_id, previous["revision"], values)
            else:
                result = await repo.add(uow, mapping_id, values)
            await append_audit(
                uow,
                context,
                audit_id,
                "release.switch",
                row["resource_type"],
                row["resource_id"],
                {
                    "version_id": version_id,
                    "previous_version_id": previous["version_id"] if previous else None,
                },
            )
        return result

    @staticmethod
    def snapshot_id(scope: Scope, run_id: str) -> str:
        return digest([scope.model_dump(), run_id])

    @classmethod
    def snapshot_keys(cls, scope: Scope, run_id: str, version_ids: list[str]) -> list[ResourceKey]:
        return [
            content_key(scope),
            record_key(scope.channel_id, "release_snapshots", cls.snapshot_id(scope, run_id)),
            *(record_key(scope.channel_id, "resource_versions", item) for item in version_ids),
            *(
                record_key(
                    scope.channel_id,
                    "source_links",
                    digest([cls.snapshot_id(scope, run_id), kind, source]),
                )
                for kind, source in [("run", run_id), *(("version", v) for v in version_ids)]
            ),
        ]

    async def snapshot_in(
        self,
        uow: UnitOfWork,
        context: AuthContext,
        run_id: str,
        version_ids: list[str],
        purpose: Literal["production", "debug", "evaluation"],
        output_schema: dict[str, Any],
        *,
        frozen_versions: tuple[ResourceVersion, ...] | None = None,
    ) -> ReleaseSnapshot:
        """受理服务完成实时授权后，传入同一事务；预算或调度写入失败会连同快照回滚。"""
        scope = context.scope
        uow.require_scope(scope)
        for key in self.snapshot_keys(scope, run_id, version_ids):
            uow.require_lock(key)
        await DeletionGuard(scope).check(
            uow, [ContentRef("run", run_id), *(ContentRef("version", v) for v in version_ids)]
        )
        if len(version_ids) != len(set(version_ids)):
            raise ServiceError("SNAPSHOT_DUPLICATE", "快照版本不能重复", 422)
        versions = []
        repo = Repository(metadata.tables["resource_versions"], scope)
        if frozen_versions is not None:
            # 受信解析器已持久化且在受理事务核验候选，禁止重读正在变化的草稿内容。
            if [v.version_id for v in frozen_versions] != version_ids or any(
                v.channel_id != scope.channel_id for v in frozen_versions
            ):
                raise ServiceError("SNAPSHOT_INVALID", "冻结版本与受理范围不符", 422)
            versions = list(frozen_versions)
        else:
            loaded = await repo.get_many(uow.connection, version_ids)
            for version_id in version_ids:
                row = loaded.get(version_id)
                if row is None or row["state"] == "RETIRED":
                    raise ServiceError("VERSION_UNAVAILABLE", "快照版本不可用")
                versions.append(version_view(row))
        if any(set(v.dependency_version_ids) - set(version_ids) for v in versions):
            raise ServiceError("SNAPSHOT_INCOMPLETE", "快照缺少传递依赖")
        if not versions or versions[0].output_schema != output_schema:
            raise ServiceError("SNAPSHOT_SCHEMA_MISMATCH", "输出结构须来自入口版本", 422)
        validate_schema(output_schema)
        snapshot = ReleaseSnapshot(
            snapshot_id=self.snapshot_id(scope, run_id),
            scope=scope,
            run_id=run_id,
            purpose=purpose,
            versions=tuple(versions),
            dependencies_digest=digest([v.model_dump(mode="json") for v in versions]),
            output_schema=output_schema,
            captured_at=utcnow(),
        )
        await Repository(metadata.tables["release_snapshots"], scope).add(
            uow,
            snapshot.snapshot_id,
            {
                "run_id": run_id,
                "purpose": purpose,
                "versions": [v.model_dump(mode="json") for v in versions],
                "dependencies_digest": snapshot.dependencies_digest,
                "output_schema": output_schema,
            },
        )
        await DeletionGuard(scope).link_many(
            uow,
            [
                (
                    digest([snapshot.snapshot_id, source.resource_type, source.resource_id]),
                    source,
                    ContentRef("snapshot", snapshot.snapshot_id),
                    None,
                )
                for source in [
                    ContentRef("run", run_id),
                    *(ContentRef("version", v) for v in version_ids),
                ]
            ],
        )
        return snapshot

    async def read_version(self, context: AuthContext, version_id: str) -> ResourceVersion:
        assert_external_io_allowed()
        await self.authorization.require(context, "version:read", version_id)
        scope = context.scope
        async with transaction(self.engine, scope, [content_key(scope)]) as uow:
            await DeletionGuard(scope).check(uow, [ContentRef("version", version_id)])
            row = await Repository(metadata.tables["resource_versions"], scope).get(
                uow.connection, version_id
            )
            if row is None:
                raise ServiceError("NOT_FOUND", "版本不存在", 404)
            return version_view(row)

    async def read_snapshot(self, context: AuthContext, snapshot_id: str) -> ReleaseSnapshot:
        assert_external_io_allowed()
        await self.authorization.require(context, "snapshot:read", snapshot_id)
        scope = context.scope
        async with transaction(self.engine, scope, [content_key(scope)]) as uow:
            await DeletionGuard(scope).check(uow, [ContentRef("snapshot", snapshot_id)])
            row = await Repository(metadata.tables["release_snapshots"], scope).get(
                uow.connection, snapshot_id
            )
            if row is None:
                raise ServiceError("NOT_FOUND", "快照不存在", 404)
            return ReleaseSnapshot(
                snapshot_id=row["id"],
                scope=scope,
                run_id=row["run_id"],
                purpose=row["purpose"],
                versions=tuple(ResourceVersion.model_validate(v) for v in row["versions"]),
                dependencies_digest=row["dependencies_digest"],
                output_schema=row["output_schema"],
                captured_at=row["created_at"],
            )
