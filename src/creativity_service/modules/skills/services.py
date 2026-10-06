"""技能包草稿、不可变发布、受控存储与加载测试服务。"""

import asyncio
from typing import Any, Literal, Protocol

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncEngine

from creativity_service.core.artifacts import ArtifactService, ObjectStore
from creativity_service.core.context import AuthContext, Scope
from creativity_service.core.contracts import (
    Artifact,
    DisplayStatus,
    ResourceVersion,
    VisibleAction,
)
from creativity_service.core.database import Repository, UnitOfWork, transaction
from creativity_service.core.database.tables import metadata as core_metadata
from creativity_service.core.deletion import ContentRef, DeletionGuard, DeletionService, content_key
from creativity_service.core.locking import ResourceKey, record_key
from creativity_service.core.observability.audit import append_audit
from creativity_service.core.primitives import ServiceError, digest, new_id, utcnow
from creativity_service.core.versioning import VersionService
from creativity_service.modules.agents.access import locked_policy, locked_require
from creativity_service.modules.iam.authorization import IamAuthorization
from creativity_service.modules.iam.reading import require_action, resource_state, visible_actions
from creativity_service.modules.iam.repositories import policy_key
from creativity_service.modules.skills.authorization import PackageAuthorization, SkillAuthorization
from creativity_service.modules.skills.dependencies import (
    local_bindings,
    local_bindings_many,
    resolve_dependencies,
)
from creativity_service.modules.skills.loader import PLACEHOLDER, ResolvedSkill, SkillLoader
from creativity_service.modules.skills.packages import (
    MAX_ARCHIVE,
    Package,
    check_export,
    decode_archive,
    entry,
    unpack,
    validate_files,
)
from creativity_service.modules.skills.repositories import repository
from creativity_service.modules.skills.schemas import (
    SkillAgentOption,
    SkillBinding,
    SkillCreate,
    SkillDefinition,
    SkillDependencySummary,
    SkillDetail,
    SkillEdit,
    SkillFileContent,
    SkillImport,
    SkillImportPreview,
    SkillImportPreviewInput,
    SkillIssue,
    SkillList,
    SkillLoadRequest,
    SkillReference,
    SkillRelease,
    SkillSettings,
    SkillTestInput,
    SkillTestView,
    SkillToolOption,
    SkillValidation,
    SkillVersionCreate,
    SkillVersionEdit,
    SkillVersionSummary,
    SkillVersionView,
    SkillView,
)
from creativity_service.modules.skills.tables import metadata
from creativity_service.modules.tools.schemas import ToolDefinition
from creativity_service.modules.tools.services import ToolService
from creativity_service.modules.tools.tables import metadata as tool_metadata


def status(value: str) -> DisplayStatus:
    labels = {
        "ACTIVE": "已启用",
        "DISABLED": "已停用",
        "DRAFT": "草稿",
        "PUBLISHED": "已冻结",
        "RETIRED": "已归档",
    }
    return DisplayStatus(
        value=value,
        label=labels.get(value, "状态不可用"),
        tone="success" if value in {"ACTIVE", "PUBLISHED"} else "default",
    )


class SkillVersions(VersionService):
    @staticmethod
    def keys(scope: Scope, version_id: str, audit_id: str) -> list[ResourceKey]:
        return [
            *VersionService.keys(scope, version_id, audit_id),
            policy_key(scope.channel_id),
            policy_key("system"),
        ]


class SkillVersionValidator:
    def __init__(self, service: "SkillService") -> None:
        self.service = service

    async def validate(
        self,
        uow: UnitOfWork,
        context: AuthContext,
        version: ResourceVersion,
        operation: Literal["freeze", "release"],
    ) -> None:
        await self.validate_many(uow, context, [version])

    async def validate_many(
        self,
        uow: UnitOfWork,
        context: AuthContext,
        versions: list[ResourceVersion],
        *,
        known_versions: dict[str, dict[str, Any]] | None = None,
        resources: dict[str, dict[str, dict[str, Any]]] | None = None,
    ) -> None:
        """依赖闭包中的技能共用一批当前包、清单、工具与授权数据。"""
        if not versions:
            return
        from creativity_service.modules.models.tables import metadata as models_metadata

        scope = context.scope
        definitions = [SkillDefinition.model_validate(v.content) for v in versions]
        skills = (
            resources["skill"]
            if resources is not None
            else await repository("skills", scope).get_many(
                uow.connection, [v.resource_id for v in versions]
            )
        )
        artifacts = core_metadata.tables["artifacts"]
        stored = [
            dict(row)
            for row in (
                await uow.connection.execute(
                    select(artifacts).where(
                        artifacts.c.channel_id == scope.channel_id,
                        artifacts.c.id.in_([d.artifact_id for d in definitions]),
                    )
                )
            ).mappings()
        ]
        indexed_artifacts = {row["id"]: row for row in stored}
        if len(indexed_artifacts) != len(stored):
            raise ServiceError("STORAGE_INVARIANT_BROKEN", "技能包标识重复", 503)
        files = await repository("skill_files", scope).find_many(
            uow.connection, "version_id", [v.version_id for v in versions]
        )
        file_index: dict[str, dict[str, dict[str, Any]]] = {}
        for file in files:
            entries = file_index.setdefault(file["version_id"], {})
            if file["relative_path"] in entries:
                raise ServiceError("SKILL_PACKAGE_CORRUPT", "技能文件路径重复", 503)
            entries[file["relative_path"]] = file
        tool_ids = {
            identifier
            for d in definitions
            for identifier in [*d.required_tool_versions, *d.tool_bindings.values()]
        }
        tool_versions = (
            known_versions
            if known_versions is not None
            else await repository("resource_versions", scope).get_many(uow.connection, tool_ids)
        )
        tool_rows = (
            resources.get("tool", {})
            if resources is not None
            else await Repository(tool_metadata.tables["tools"], scope).get_many(
                uow.connection,
                [v["resource_id"] for v in tool_versions.values() if v["resource_type"] == "tool"],
            )
        )
        agent_ids = {identifier for d in definitions for identifier in d.allowed_agents}
        version_repo = repository("resource_versions", scope)
        known_agents = (
            set(
                await uow.connection.scalars(
                    select(version_repo.table.c.resource_id)
                    .where(
                        version_repo.predicate(),
                        version_repo.table.c.resource_type == "agent",
                        version_repo.table.c.resource_id.in_(agent_ids),
                    )
                    .distinct()
                )
            )
            if agent_ids
            else set()
        )
        needs_models = any(d.required_model_capabilities for d in definitions)
        models = (
            await Repository(models_metadata.tables["models"], scope).find(
                uow.connection, status="ACTIVE"
            )
            if needs_models
            else []
        )
        connections = await Repository(models_metadata.tables["model_connections"], scope).get_many(
            uow.connection, [m["connection_id"] for m in models]
        )
        data = {
            "versions": tool_versions,
            "tools": tool_rows,
            "agents": known_agents,
            "models": models,
            "connections": connections,
        }
        policy = await locked_policy(uow, context)
        refs = []
        for version, definition in zip(versions, definitions, strict=True):
            row = skills.get(version.resource_id)
            if version.resource_type != "skill" or not row or row["status"] != "ACTIVE":
                raise ServiceError("SKILL_UNAVAILABLE", "技能不存在或已停用", 409)
            artifact = indexed_artifacts.get(definition.artifact_id)
            if (
                not artifact
                or artifact["state"] != "AVAILABLE"
                or artifact["expires_at"] <= utcnow()
            ):
                raise ServiceError("SKILL_PACKAGE_CORRUPT", "技能包文件不存在或已失效", 409)
            ids, _, issues = await resolve_dependencies(
                uow.connection, context, definition, self.service.tools, loaded=data
            )
            if issues:
                raise ServiceError(issues[0].code, "；".join(i.message for i in issues), 422)
            if ids != sorted(definition.required_tool_versions) or ids != sorted(
                version.dependency_version_ids
            ):
                raise ServiceError(
                    "SKILL_DEPENDENCY_MISSING", "工具绑定已变化，请重新保存草稿", 409
                )
            for identifier in ids:
                tool_version = tool_versions.get(identifier)
                if not tool_version or tool_version["resource_type"] != "tool":
                    raise ServiceError("SKILL_DEPENDENCY_MISSING", "工具版本不存在", 422)
                tool = ToolDefinition.model_validate(tool_version["content"])
                for action in {"run:create", *tool.required_scopes}:
                    policy.require(context, action, "tool", tool_version["resource_id"])
            actual = file_index.get(version.version_id, {})
            if set(actual) != {f.relative_path for f in definition.files} or any(
                actual[f.relative_path]["sha256"] != f.sha256
                or actual[f.relative_path]["size_bytes"] != f.size_bytes
                or actual[f.relative_path]["artifact_id"] != definition.artifact_id
                for f in definition.files
            ):
                raise ServiceError("SKILL_PACKAGE_CORRUPT", "技能包清单不一致", 503)
            refs.extend(
                [
                    ContentRef("artifact", definition.artifact_id),
                    ContentRef("skill", row["id"]),
                    *[ContentRef("version", v) for v in ids],
                ]
            )
        await DeletionGuard(scope).check(uow, refs)


class SkillRuntimeRunner(Protocol):
    async def submit(
        self, context: AuthContext, test_id: str, version_id: str, request: SkillLoadRequest
    ) -> str:
        """将已经固定的加载测试提交为独立运行。"""
        ...


class SkillService:
    def __init__(
        self,
        engine: AsyncEngine,
        authorization: IamAuthorization,
        store: ObjectStore,
        tools: ToolService,
    ) -> None:
        self.engine, self.authorization, self.store, self.tools = (
            engine,
            authorization,
            store,
            tools,
        )
        self.access = SkillAuthorization(engine, authorization)
        self.versions = SkillVersions(engine, self.access, SkillVersionValidator(self))
        self.loader = SkillLoader(self)
        self.runtime_runner: SkillRuntimeRunner | None = None
        self.deletion = DeletionService(engine, self.access)

    async def require(self, context: AuthContext, action: str, skill_id: str) -> None:
        await self.authorization.boundary(context, action, "skill", skill_id)

    async def actions(
        self,
        context: AuthContext,
        skill_id: str,
        actions: list[tuple[str, str, str]],
        permissions: frozenset[str] | None = None,
    ) -> list[VisibleAction]:
        if permissions is None:
            permissions = await self.authorization.allowed_actions(context, "skill", skill_id)
        return visible_actions(permissions, actions)

    def artifacts(
        self, context: AuthContext, skill_id: str, action: str = "skill:manage"
    ) -> ArtifactService:
        return ArtifactService(
            self.engine,
            self.store,
            PackageAuthorization(self.authorization, context, skill_id, action),
        )

    async def raw(
        self, context: AuthContext, version_id: str
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        async with transaction(self.engine, context.scope, [content_key(context.scope)]) as uow:
            await DeletionGuard(context.scope).check(uow, [ContentRef("version", version_id)])
            version = await repository("resource_versions", context.scope).get(
                uow.connection, version_id
            )
            if not version or version["resource_type"] != "skill":
                raise ServiceError("NOT_FOUND", "技能版本不存在", 404)
            skill = await repository("skills", context.scope).get(
                uow.connection, version["resource_id"]
            )
            if not skill:
                raise ServiceError("NOT_FOUND", "技能不存在", 404)
            await DeletionGuard(context.scope).check(uow, [ContentRef("skill", skill["id"])])
        return skill, version

    async def artifact_context(self, context: AuthContext, artifact_id: str) -> AuthContext:
        """从渠道内存储记录恢复范围，并在返回上下文前复核文件删除与恢复屏障。"""
        table = core_metadata.tables["artifacts"]
        async with self.engine.connect() as connection:
            rows = (
                (
                    await connection.execute(
                        select(table).where(
                            table.c.channel_id == context.scope.channel_id,
                            table.c.id == artifact_id,
                        )
                    )
                )
                .mappings()
                .all()
            )
        if len(rows) != 1:
            raise ServiceError("NOT_FOUND", "技能包文件不存在", 404)
        storage_scope = Scope.model_validate({key: rows[0][key] for key in Scope.model_fields})
        async with transaction(self.engine, storage_scope, [content_key(storage_scope)]) as uow:
            await DeletionGuard(storage_scope).check(uow, [ContentRef("artifact", artifact_id)])
            current = await repository("artifacts", storage_scope).get(uow.connection, artifact_id)
            if not current or current["state"] != "AVAILABLE" or current["expires_at"] <= utcnow():
                raise ServiceError("NOT_FOUND", "技能包文件不存在或已失效", 404)
        return context.model_copy(update={"scope": storage_scope})

    async def package(
        self,
        context: AuthContext,
        skill_id: str,
        definition: SkillDefinition,
        action: str = "skill:manage",
    ) -> Package:
        await self.require(context, action, skill_id)
        # 配置在渠道内跨环境共享；存储归属只从服务端产物记录读取。
        storage_context = await self.artifact_context(context, definition.artifact_id)
        data, _, _ = await self.artifacts(context, skill_id, action).download(
            storage_context, definition.artifact_id
        )
        async with self.engine.connect() as connection:
            bindings = await local_bindings(connection, context, definition)
        package = await asyncio.to_thread(
            unpack,
            data,
            SkillSettings.model_validate(
                {
                    **definition.model_dump(include=set(SkillSettings.model_fields)),
                    "tool_bindings": bindings,
                }
            ),
        )
        if (
            package.package_hash != definition.package_hash
            or package.metadata != definition.metadata
        ):
            raise ServiceError("SKILL_PACKAGE_CORRUPT", "技能包摘要与实际文件不一致", 503)
        return package

    async def orphan(self, context: AuthContext, artifact_id: str) -> None:
        """补偿未登记或已替换包；保留加载快照仍引用的旧包，其他包交公共回收处理器。"""
        artifacts = core_metadata.tables["artifacts"]
        async with self.engine.connect() as connection:
            stored = (
                (
                    await connection.execute(
                        select(artifacts).where(
                            artifacts.c.channel_id == context.scope.channel_id,
                            artifacts.c.id == artifact_id,
                        )
                    )
                )
                .mappings()
                .all()
            )
        if not stored:
            return
        if len(stored) != 1:
            raise ServiceError("STORAGE_INVARIANT_BROKEN", "包产物标识重复", 503)
        storage_scope = Scope.model_validate({key: stored[0][key] for key in Scope.model_fields})
        table = metadata.tables["skill_tests"]
        async with transaction(
            self.engine,
            storage_scope,
            [
                content_key(storage_scope),
                record_key(context.scope.channel_id, "artifacts", artifact_id),
            ],
        ) as uow:
            versions = await repository("resource_versions", context.scope).find(
                uow.connection, resource_type="skill"
            )
            tests = (
                (
                    await uow.connection.execute(
                        select(table.c.context_snapshot).where(
                            table.c.channel_id == context.scope.channel_id
                        )
                    )
                )
                .scalars()
                .all()
            )
            if any(v["content"].get("artifact_id") == artifact_id for v in versions) or any(
                t.get("definition", {}).get("artifact_id") == artifact_id for t in tests
            ):
                return
            repo = repository("artifacts", storage_scope)
            row = await repo.get(uow.connection, artifact_id)
            if row and row["state"] == "AVAILABLE":
                await repo.change(
                    uow,
                    artifact_id,
                    row["revision"],
                    {"state": "STAGED", "upload_expires_at": utcnow()},
                )

    async def write_package(
        self,
        context: AuthContext,
        skill_id: str,
        version_id: str,
        label: str,
        package: Package,
        *,
        resource: dict[str, Any] | None = None,
        revision: int | None = None,
    ) -> None:
        action_id = "new" if resource is not None else skill_id
        await self.require(context, "skill:manage", action_id)
        await self.require(context, "version:edit", action_id)
        # 目标工具必须显式选择并获授权；上传前拒绝跨渠道和无权访问的引用。
        selected_tools = []
        for identifier in set(package.settings.tool_bindings.values()):
            tool = await self.tools.check_dependency(context, identifier)
            tool_definition = ToolDefinition.model_validate(tool.content)
            for action in {"run:create", *tool_definition.required_scopes}:
                await self.authorization.boundary(context, action, "tool", tool.resource_id)
            selected_tools.append(tool)
        variable_names = {v.name for v in package.settings.input_variables}
        for file in package.manifest:
            if file.loadable:
                placeholders = set(
                    PLACEHOLDER.findall(package.files[file.relative_path].decode("utf-8-sig"))
                )
                if placeholders - variable_names:
                    raise ServiceError("SKILL_VARIABLE_INVALID", "文件使用了未声明的变量", 422)
        archive = await asyncio.to_thread(package.archive)
        if len(archive) > MAX_ARCHIVE:
            raise ServiceError("SKILL_FORMAT_INVALID", "技能包压缩后超过 8 MiB", 422)
        artifact = await self.artifacts(context, action_id).upload(
            context,
            "skill-package.zip",
            "application/zip",
            archive,
            [ContentRef("skill", skill_id), ContentRef("version", version_id)],
            retention_seconds=36500 * 86400,
        )
        scope, audit_id = context.scope, new_id("audit")
        old_artifact: str | None = None
        try:
            await self.require(context, "skill:manage", action_id)
            await self.require(context, "version:edit", action_id)
            async with self.engine.connect() as connection:
                old_files = await repository("skill_files", scope).find(
                    connection, version_id=version_id
                )
            file_ids = {f.relative_path: new_id("skill_file") for f in package.manifest}
            link_id = digest(["skill-version", version_id])
            file_links = {path: new_id("source") for path in file_ids}
            keys = [
                content_key(scope),
                policy_key(scope.channel_id),
                policy_key("system"),
                *[
                    record_key(scope.channel_id, "resource_versions", t.version_id)
                    for t in selected_tools
                ],
                *[record_key(scope.channel_id, "tools", t.resource_id) for t in selected_tools],
                record_key(scope.channel_id, "skills", skill_id),
                record_key(scope.channel_id, "resource_versions", version_id),
                record_key(scope.channel_id, "audit_events", audit_id),
                record_key(scope.channel_id, "source_links", link_id),
                *[
                    record_key(scope.channel_id, "source_links", item)
                    for item in file_links.values()
                ],
                ResourceKey(scope.channel_id, "version-label", ("skill", skill_id, label)),
                *[
                    record_key(scope.channel_id, "skill_files", f)
                    for f in [*file_ids.values(), *[f["id"] for f in old_files]]
                ],
            ]
            if resource:
                keys.append(ResourceKey(scope.channel_id, "skill-code", (resource["skill_code"],)))
            async with transaction(self.engine, scope, keys) as uow:
                guard = DeletionGuard(scope)
                await guard.check(
                    uow,
                    [
                        ContentRef("skill", skill_id),
                        ContentRef("version", version_id),
                        ContentRef("artifact", artifact.artifact_id),
                    ],
                )
                repo = repository("resource_versions", scope)
                current = await repo.get(uow.connection, version_id)
                if revision is not None:
                    if not current or current["revision"] != revision:
                        raise ServiceError("REVISION_CONFLICT", "技能草稿已变更，请刷新", 409)
                    if current["state"] != "DRAFT":
                        raise ServiceError("VERSION_FROZEN", "已冻结版本不能修改", 409)
                    old_artifact = current["content"]["artifact_id"]
                elif await repo.find(
                    uow.connection, resource_type="skill", resource_id=skill_id, version_label=label
                ):
                    raise ServiceError("VERSION_LABEL_CONFLICT", "版本名称已存在", 409)
                if resource:
                    skills = repository("skills", scope)
                    if await skills.find(uow.connection, skill_code=resource["skill_code"]):
                        raise ServiceError("CODE_EXISTS", "技能编码已存在", 409)
                    await skills.add(uow, skill_id, {**resource, "status": "ACTIVE"})
                elif not await repository("skills", scope).get(uow.connection, skill_id):
                    raise ServiceError("NOT_FOUND", "技能不存在", 404)
                ids, _, issues = await resolve_dependencies(
                    uow.connection, context, package.settings, self.tools
                )
                invalid_bindings = [i for i in issues if i.path in package.settings.tool_bindings]
                if invalid_bindings:
                    raise ServiceError(
                        "SKILL_DEPENDENCY_MISSING",
                        "；".join(i.message for i in invalid_bindings),
                        422,
                    )
                await self.check_tool_permissions(uow, context, ids)
                definition = SkillDefinition(
                    **package.settings.model_dump(),
                    package_hash=package.package_hash,
                    metadata=package.metadata,
                    files=package.manifest,
                    artifact_id=artifact.artifact_id,
                    required_tool_versions=tuple(ids),
                )
                content = definition.model_dump(mode="json")
                values = {
                    "content": content,
                    "content_digest": digest({"content": content, "output_schema": {}}),
                    "dependencies": ids,
                    "dependencies_digest": digest(ids),
                    "output_schema": {},
                }
                if current:
                    await repo.change(uow, version_id, current["revision"], values)
                else:
                    await repo.add(
                        uow,
                        version_id,
                        {
                            **values,
                            "resource_type": "skill",
                            "resource_id": skill_id,
                            "version_label": label,
                            "state": "DRAFT",
                            "created_by": context.principal_id,
                        },
                    )
                    await guard.link(
                        uow,
                        link_id,
                        ContentRef("skill", skill_id),
                        ContentRef("version", version_id),
                    )
                for old_file in old_files:
                    await repository("skill_files", scope).remove(uow, old_file["id"])
                for file in package.manifest:
                    await repository("skill_files", scope).add(
                        uow,
                        file_ids[file.relative_path],
                        {
                            **file.model_dump(),
                            "version_id": version_id,
                            "artifact_id": artifact.artifact_id,
                        },
                    )
                    await guard.link(
                        uow,
                        file_links[file.relative_path],
                        ContentRef("version", version_id),
                        ContentRef("skill_file", file_ids[file.relative_path]),
                    )
                await append_audit(
                    uow,
                    context,
                    audit_id,
                    "skill.package.save",
                    "skill",
                    skill_id,
                    {"version_id": version_id, "content_digest": package.package_hash},
                )
        except BaseException:
            await self.orphan(context, artifact.artifact_id)
            raise
        if old_artifact:
            await self.orphan(context, old_artifact)

    async def create(self, context: AuthContext, body: SkillCreate) -> SkillDetail:
        package = validate_files(
            {"SKILL.md": entry(body.name, body.description, body.instructions)}, body.settings
        )
        skill_id, version_id = new_id("skill"), new_id("version")
        await self.write_package(
            context,
            skill_id,
            version_id,
            body.version_label,
            package,
            resource=body.model_dump(
                mode="json", include={"skill_code", "name", "description", "owner", "tags"}
            ),
        )
        return await self.detail(context, skill_id)

    async def import_package(self, context: AuthContext, body: SkillImport) -> SkillDetail:
        await self.require(context, "skill:manage", "new")
        package = await asyncio.to_thread(unpack, decode_archive(body.archive_base64))
        package = validate_files(
            package.files, package.settings.model_copy(update={"tool_bindings": body.tool_bindings})
        )
        skill_id = new_id("skill")
        await self.write_package(
            context,
            skill_id,
            new_id("version"),
            body.version_label,
            package,
            resource={
                "skill_code": body.skill_code,
                "name": body.name or package.metadata["name"],
                "description": package.metadata["description"],
                "owner": body.owner,
                "tags": [],
            },
        )
        return await self.detail(context, skill_id)

    async def preview_import(
        self, context: AuthContext, body: SkillImportPreviewInput
    ) -> SkillImportPreview:
        await self.require(context, "skill:manage", "new")
        package = await asyncio.to_thread(unpack, decode_archive(body.archive_base64))
        return SkillImportPreview(
            metadata=package.metadata,
            files=list(package.manifest),
            settings=package.settings,
            instruction_preview=package.instructions,
        )

    async def check_tool_permissions(
        self, uow: UnitOfWork, context: AuthContext, identifiers: list[str]
    ) -> None:
        """与绑定写入、冻结处于同一授权锁和事务，防止校验后撤销的竞态。"""
        for identifier in identifiers:
            version = await repository("resource_versions", context.scope).get(
                uow.connection, identifier
            )
            if not version or version["resource_type"] != "tool":
                raise ServiceError("SKILL_DEPENDENCY_MISSING", "工具版本不存在", 422)
            definition = ToolDefinition.model_validate(version["content"])
            for action in {"run:create", *definition.required_scopes}:
                await locked_require(uow, context, action, "tool", version["resource_id"])

    async def view(
        self, context: AuthContext, row: dict[str, Any], permissions: frozenset[str] | None = None
    ) -> SkillView:
        return SkillView(
            skill_id=row["id"],
            skill_code=row["skill_code"],
            name=row["name"],
            description=row["description"],
            owner=row["owner"],
            tags=row["tags"],
            revision=row["revision"],
            status=status(row["status"]),
            actions=await self.actions(
                context,
                row["id"],
                [
                    ("edit", "编辑技能", "skill:manage"),
                    ("create_version", "新增版本", "version:edit"),
                ],
                permissions,
            ),
        )

    async def list_skills(self, context: AuthContext, search: str | None = None) -> SkillList:
        policy = await self.authorization.read_policy(context)
        items = []
        async with transaction(self.engine, context.scope, [content_key(context.scope)]) as uow:
            rows = await repository("skills", context.scope).find(uow.connection)
            rows = [
                row
                for row in rows
                if (
                    not search or search.casefold() in (row["name"] + row["description"]).casefold()
                )
                and "skill:manage"
                in policy.actions("skill", row["id"], resource_state(context, "skill", row))
            ]
            blocked = await DeletionGuard(context.scope).blocked_refs(
                uow, [ContentRef("skill", row["id"]) for row in rows]
            )
            for row in rows:
                if ContentRef("skill", row["id"]) not in blocked:
                    items.append(await self.view(context, row, frozenset()))
        return SkillList(
            items=items,
            actions=visible_actions(
                policy.actions("skill", "new"),
                [("create", "新增技能", "skill:manage"), ("import", "导入技能包", "skill:manage")],
            ),
        )

    async def version(self, context: AuthContext, version_id: str) -> SkillVersionView:
        row, version = await self.raw(context, version_id)
        await self.require(context, "skill:manage", row["id"])
        definition = SkillDefinition.model_validate(version["content"])
        package = await self.package(context, row["id"], definition)
        actions = [
            ("validate", "检查依赖", "skill:manage"),
            ("test", "加载测试", "skill:manage"),
            ("export", "导出技能包", "data:export"),
        ]
        if version["state"] == "DRAFT":
            actions += [
                ("edit", "编辑包", "version:edit"),
                ("freeze", "冻结版本", "version:freeze"),
            ]
        elif version["state"] == "PUBLISHED":
            actions.append(("release", "发布到当前环境", "release:publish"))
        return SkillVersionView(
            version_id=version_id,
            version_label=version["version_label"],
            revision=version["revision"],
            status=status(version["state"]),
            settings=package.settings,
            metadata=package.metadata,
            files=list(package.manifest),
            package_hash=package.package_hash,
            discovery_preview=f"{package.metadata['name']}\n{package.metadata['description']}",
            instruction_preview=package.instructions,
            actions=await self.actions(context, row["id"], actions),
        )

    async def detail(self, context: AuthContext, skill_id: str) -> SkillDetail:
        from creativity_service.modules.agents.repositories import (
            RESOURCE_TABLES,
        )
        from creativity_service.modules.agents.repositories import (
            repository as resources,
        )

        policy = await self.authorization.read_policy(context)
        async with transaction(self.engine, context.scope, [content_key(context.scope)]) as uow:
            row = await repository("skills", context.scope).get(uow.connection, skill_id)
            if not row:
                raise ServiceError("NOT_FOUND", "技能不存在", 404)
            permissions = policy.actions("skill", skill_id, resource_state(context, "skill", row))
            require_action(permissions, "skill:manage")
            versions = await repository("resource_versions", context.scope).find(
                uow.connection, resource_type="skill", resource_id=skill_id
            )
            await DeletionGuard(context.scope).check(
                uow,
                [
                    ContentRef("skill", skill_id),
                    *(ContentRef("version", v["id"]) for v in versions),
                ],
            )
            versions.sort(key=lambda v: v["created_at"])
            definitions = [SkillDefinition.model_validate(v["content"]) for v in versions]
            bindings = await local_bindings_many(uow.connection, context, definitions)
            mappings = await repository("release_mappings", context.scope).find(
                uow.connection, resource_type="skill", resource_id=skill_id
            )
            refs = await repository("resource_references", context.scope).find_many(
                uow.connection, "target_version_id", [v["id"] for v in versions]
            )
            source_rows = await repository("resource_versions", context.scope).get_many(
                uow.connection, [r["source_version_id"] for r in refs]
            )
            parents = {
                kind: await resources(table, context.scope).get_many(
                    uow.connection,
                    [r["resource_id"] for r in source_rows.values() if r["resource_type"] == kind],
                )
                for kind, table in RESOURCE_TABLES.items()
                if any(r["resource_type"] == kind for r in source_rows.values())
            }
        summaries = []
        for version, definition, local in zip(versions, definitions, bindings, strict=True):
            actions = [
                ("validate", "检查依赖", "skill:manage"),
                ("test", "加载测试", "skill:manage"),
                ("export", "导出技能包", "data:export"),
            ]
            if version["state"] == "DRAFT":
                actions += [
                    ("edit", "编辑包", "version:edit"),
                    ("freeze", "冻结版本", "version:freeze"),
                ]
            elif version["state"] == "PUBLISHED":
                actions.append(("release", "发布到当前环境", "release:publish"))
            summaries.append(
                SkillVersionSummary(
                    version_id=version["id"],
                    version_label=version["version_label"],
                    revision=version["revision"],
                    status=status(version["state"]),
                    settings=SkillSettings.model_validate(
                        {
                            **definition.model_dump(include=set(SkillSettings.model_fields)),
                            "tool_bindings": local,
                        }
                    ),
                    metadata=definition.metadata,
                    files=list(definition.files),
                    package_hash=definition.package_hash,
                    discovery_preview=f"{definition.metadata['name']}\n{definition.metadata['description']}",
                    actions=visible_actions(permissions, actions),
                )
            )
        return SkillDetail(
            skill=await self.view(context, row, permissions),
            versions=summaries,
            references=[
                SkillReference(
                    version_id=r["id"],
                    resource_name=parents.get(r["resource_type"], {})
                    .get(r["resource_id"], {})
                    .get("name"),
                    version_label=r["version_label"],
                    status=status(r["state"]),
                )
                for r in source_rows.values()
            ],
            release_version_id=mappings[0]["version_id"] if mappings else None,
            release_revision=mappings[0]["revision"] if mappings else None,
        )

    async def edit(self, context: AuthContext, skill_id: str, body: SkillEdit) -> SkillDetail:
        await self.require(context, "skill:manage", skill_id)
        audit_id = new_id("audit")
        async with transaction(
            self.engine,
            context.scope,
            [
                content_key(context.scope),
                record_key(context.scope.channel_id, "skills", skill_id),
                record_key(context.scope.channel_id, "audit_events", audit_id),
            ],
        ) as uow:
            await DeletionGuard(context.scope).check(uow, [ContentRef("skill", skill_id)])
            await repository("skills", context.scope).change(
                uow, skill_id, body.revision, body.model_dump(mode="json", exclude={"revision"})
            )
            await append_audit(
                uow, context, audit_id, "skill.edit", "skill", skill_id, {"state": body.status}
            )
        return await self.detail(context, skill_id)

    async def create_version(
        self, context: AuthContext, skill_id: str, body: SkillVersionCreate
    ) -> SkillVersionView:
        skill, version = await self.raw(context, body.base_version_id)
        if skill["id"] != skill_id:
            raise ServiceError("NOT_FOUND", "技能版本不存在", 404)
        package = await self.package(
            context, skill_id, SkillDefinition.model_validate(version["content"])
        )
        version_id = new_id("version")
        await self.write_package(context, skill_id, version_id, body.version_label, package)
        return await self.version(context, version_id)

    async def edit_version(
        self, context: AuthContext, version_id: str, body: SkillVersionEdit
    ) -> SkillVersionView:
        skill, version = await self.raw(context, version_id)
        await self.require(context, "version:edit", skill["id"])
        if version["state"] != "DRAFT":
            raise ServiceError("VERSION_FROZEN", "已冻结版本不能修改", 409)
        if version["revision"] != body.revision:
            raise ServiceError("REVISION_CONFLICT", "技能草稿已变更", 409)
        package = await self.package(
            context, skill["id"], SkillDefinition.model_validate(version["content"])
        )
        paths = [f.relative_path for f in body.files]
        if len(paths) != len(set(paths)) or set(paths).intersection(body.remove_paths):
            raise ServiceError("SKILL_FORMAT_INVALID", "编辑包含重复或冲突文件路径", 422)
        files = {p: v for p, v in package.files.items() if p not in body.remove_paths}
        files.update({f.relative_path: f.text.encode() for f in body.files})
        package = validate_files(files, body.settings)
        await self.write_package(
            context,
            skill["id"],
            version_id,
            version["version_label"],
            package,
            revision=body.revision,
        )
        return await self.version(context, version_id)

    async def validate(self, context: AuthContext, version_id: str) -> SkillValidation:
        skill, version = await self.raw(context, version_id)
        await self.require(context, "skill:manage", skill["id"])
        definition = SkillDefinition.model_validate(version["content"])
        await self.package(context, skill["id"], definition)
        async with self.engine.connect() as connection:
            ids, deps, issues = await resolve_dependencies(
                connection, context, definition, self.tools
            )
        if ids != sorted(definition.required_tool_versions):
            issues.append(
                SkillIssue(
                    code="SKILL_DEPENDENCY_MISSING", message="目标依赖已变化，请保存草稿以重新绑定"
                )
            )
        if skill["status"] != "ACTIVE":
            issues.append(SkillIssue(code="SKILL_UNAVAILABLE", message="技能已停用"))
        for dep in deps:
            if dep.version_id:
                try:
                    tool = await self.tools.check_dependency(context, dep.version_id)
                    for action in {
                        "run:create",
                        *ToolDefinition.model_validate(tool.content).required_scopes,
                    }:
                        await self.authorization.boundary(context, action, "tool", tool.resource_id)
                except ServiceError as exc:
                    issues.append(SkillIssue(code="SKILL_DEPENDENCY_MISSING", message=exc.message))
        return SkillValidation(
            valid=not issues, issues=issues, dependencies=deps, package_hash=definition.package_hash
        )

    async def freeze(
        self, context: AuthContext, version_id: str, revision: int
    ) -> SkillVersionView:
        validation = await self.validate(context, version_id)
        if not validation.valid:
            raise ServiceError(
                "SKILL_DEPENDENCY_MISSING", "；".join(i.message for i in validation.issues), 422
            )
        await self.versions.freeze(context, version_id, revision)
        return await self.version(context, version_id)

    async def release(self, context: AuthContext, skill_id: str, body: SkillRelease) -> SkillDetail:
        skill, _ = await self.raw(context, body.version_id)
        if skill["id"] != skill_id:
            raise ServiceError("NOT_FOUND", "技能版本不存在", 404)
        validation = await self.validate(context, body.version_id)
        if not validation.valid:
            raise ServiceError(
                "SKILL_DEPENDENCY_MISSING", "；".join(i.message for i in validation.issues), 422
            )
        await self.versions.release(context, body.version_id, body.expected_revision, body.note)
        return await self.detail(context, skill_id)

    async def file(self, context: AuthContext, version_id: str, path: str) -> SkillFileContent:
        skill, version = await self.raw(context, version_id)
        package = await self.package(
            context, skill["id"], SkillDefinition.model_validate(version["content"])
        )
        if path not in package.files:
            raise ServiceError("NOT_FOUND", "包内文件不存在", 404)
        file = next(f for f in package.manifest if f.relative_path == path)
        try:
            text = package.files[path].decode("utf-8-sig")
        except UnicodeDecodeError:
            text = None
        return SkillFileContent(path=path, text=text, unavailable_reason=file.unavailable_reason)

    async def export(self, context: AuthContext, version_id: str) -> Artifact:
        skill, version = await self.raw(context, version_id)
        await self.require(context, "data:export", skill["id"])
        await self.require(context, "data:read_sensitive", skill["id"])
        package = await self.package(
            context, skill["id"], SkillDefinition.model_validate(version["content"])
        )
        check_export(package)
        archive = await asyncio.to_thread(package.archive, portable=True)
        return await self.artifacts(context, skill["id"], "data:export").upload(
            context,
            f"{skill['skill_code']}.zip",
            "application/zip",
            archive,
            [ContentRef("version", version_id)],
            retention_seconds=86400,
        )

    async def resolve(self, context: AuthContext, version_id: str, purpose: str) -> ResolvedSkill:
        skill, version = await self.raw(context, version_id)
        await self.require(
            context, "run:create" if purpose == "runtime" else "skill:manage", skill["id"]
        )
        definition = SkillDefinition.model_validate(version["content"])
        async with self.engine.connect() as connection:
            ids, _, issues = await resolve_dependencies(connection, context, definition, self.tools)
        if issues or ids != sorted(definition.required_tool_versions):
            raise ServiceError(
                "SKILL_DEPENDENCY_MISSING",
                "；".join(i.message for i in issues) or "工具依赖已变化",
                409,
            )
        if ids:
            policy = await self.authorization.read_policy(context)
            async with self.engine.connect() as connection:
                versions = await repository("resource_versions", context.scope).get_many(
                    connection, ids
                )
                tools = await Repository(tool_metadata.tables["tools"], context.scope).get_many(
                    connection, [v["resource_id"] for v in versions.values()]
                )
            if set(ids) - versions.keys():
                raise ServiceError("SKILL_DEPENDENCY_MISSING", "工具依赖已变化", 409)
            for tool in versions.values():
                row = tools.get(tool["resource_id"])
                if row is None:
                    raise ServiceError("SKILL_DEPENDENCY_MISSING", "工具依赖已变化", 409)
                actions = policy.actions("tool", row["id"], resource_state(context, "tool", row))
                require_action(actions, "run:create")
                for action in ToolDefinition.model_validate(tool["content"]).required_scopes:
                    require_action(actions, action)
        return ResolvedSkill(
            skill["id"],
            version_id,
            version["revision"],
            version["state"],
            skill["status"] == "ACTIVE",
            definition,
        )

    async def recheck(self, context: AuthContext, skill: ResolvedSkill, purpose: str) -> None:
        current = await self.resolve(context, skill.version_id, purpose)
        await self.artifact_context(context, skill.definition.artifact_id)
        if not current.active or current.revision != skill.revision:
            raise ServiceError("SKILL_UNAVAILABLE", "技能状态或版本内容已变化", 409)

    async def read_files(
        self, context: AuthContext, skill: ResolvedSkill, paths: tuple[str, ...], purpose: str
    ) -> dict[str, bytes]:
        package = await self.package(
            context,
            skill.skill_id,
            skill.definition,
            "run:create" if purpose == "runtime" else "skill:manage",
        )
        return {path: package.files[path] for path in paths}

    async def dependency_summary(
        self, context: AuthContext, version_id: str
    ) -> SkillDependencySummary:
        skill = await self.resolve(context, version_id, "runtime")
        if not skill.active or skill.state != "PUBLISHED":
            raise ServiceError("SKILL_UNAVAILABLE", "只能绑定已启用的冻结技能版本", 409)
        _, row = await self.raw(context, version_id)
        return SkillDependencySummary(
            channel_id=context.scope.channel_id,
            skill_id=skill.skill_id,
            version_id=version_id,
            content_digest=row["content_digest"],
            package_hash=skill.definition.package_hash,
            dependencies_digest=row["dependencies_digest"],
            required_tool_versions=skill.definition.required_tool_versions,
            required_model_capabilities=skill.definition.required_model_capabilities,
        )

    async def test(
        self, context: AuthContext, version_id: str, body: SkillTestInput
    ) -> SkillTestView:
        if body.execute_scripts:
            raise ServiceError(
                "SKILL_EXECUTION_UNSUPPORTED", "请将已冻结技能脚本绑定隔离工具后发起测试", 422
            )
        skill, version = await self.raw(context, version_id)
        await self.require(context, "skill:manage", skill["id"])
        if version["revision"] != body.revision:
            raise ServiceError("REVISION_CONFLICT", "技能版本已变更", 409)
        definition = SkillDefinition.model_validate(version["content"])
        request = SkillLoadRequest(
            bindings=(
                SkillBinding(
                    version_id=version_id,
                    selected=body.selected,
                    selected_files=body.selected_files,
                    variables=body.variables,
                    trigger_reason="加载测试",
                ),
            ),
            authorized_tool_versions=definition.required_tool_versions,
            model_capabilities=definition.required_model_capabilities,
            context_budget=body.context_budget,
            purpose="test",
        )
        result = await self.loader.load(context, request)
        test_id, link_id = new_id("skill_test"), new_id("source")
        async with transaction(
            self.engine,
            context.scope,
            [
                content_key(context.scope),
                record_key(context.scope.channel_id, "skill_tests", test_id),
                record_key(context.scope.channel_id, "source_links", link_id),
            ],
        ) as uow:
            current = await repository("resource_versions", context.scope).get(
                uow.connection, version_id
            )
            if not current or current["revision"] != body.revision:
                raise ServiceError("REVISION_CONFLICT", "加载期间版本已变化", 409)
            guard = DeletionGuard(context.scope)
            await guard.link(
                uow, link_id, ContentRef("version", version_id), ContentRef("skill_test", test_id)
            )
            row = await repository("skill_tests", context.scope).add(
                uow,
                test_id,
                {
                    "version_id": version_id,
                    "release_snapshot_id": None,
                    "run_id": None,
                    "context_snapshot": {
                        "definition": definition.model_dump(mode="json"),
                        "request": request.model_dump(mode="json"),
                        "content_digest": version["content_digest"],
                        "version_label": version["version_label"],
                    },
                    "selected_files": [f.path for f in result.loaded],
                    "result": result.model_dump(mode="json"),
                },
            )
        run_id = None
        # 包验证检查确定性加载；业务运行仍须执行授权。
        if self.runtime_runner is not None:
            run_id = await self.runtime_runner.submit(context, test_id, version_id, request)
            async with transaction(
                self.engine,
                context.scope,
                [
                    content_key(context.scope),
                    record_key(context.scope.channel_id, "skill_tests", test_id),
                ],
            ) as uow:
                await DeletionGuard(context.scope).check(uow, [ContentRef("skill_test", test_id)])
                current_test = await repository("skill_tests", context.scope).get(
                    uow.connection, test_id
                )
                assert current_test is not None
                await repository("skill_tests", context.scope).change(
                    uow, test_id, current_test["revision"], {"run_id": run_id}
                )
        return SkillTestView(
            test_id=test_id,
            version_id=version_id,
            version_label=version["version_label"],
            created_at=row["created_at"].isoformat(),
            result=result,
            run_id=run_id,
        )

    async def tests(self, context: AuthContext, version_id: str) -> list[SkillTestView]:
        skill, _ = await self.raw(context, version_id)
        await self.require(context, "skill:manage", skill["id"])
        result = []
        async with transaction(self.engine, context.scope, [content_key(context.scope)]) as uow:
            rows = await repository("skill_tests", context.scope).find(
                uow.connection, version_id=version_id
            )
            for row in sorted(rows, key=lambda r: r["created_at"], reverse=True):
                try:
                    await DeletionGuard(context.scope).check(
                        uow, [ContentRef("skill_test", row["id"])]
                    )
                except ServiceError as exc:
                    if exc.code == "CONTENT_DELETED":
                        continue
                    raise
                result.append(
                    SkillTestView(
                        test_id=row["id"],
                        version_id=version_id,
                        version_label=row["context_snapshot"]["version_label"],
                        created_at=row["created_at"].isoformat(),
                        result=row["result"],
                        run_id=row["run_id"],
                    )
                )
        return result

    async def tool_options(self, context: AuthContext) -> list[SkillToolOption]:
        from creativity_service.modules.agents.repositories import repository as resources

        policy = await self.authorization.read_policy(context)
        async with transaction(self.engine, context.scope, [content_key(context.scope)]) as uow:
            versions = await repository("resource_versions", context.scope).find(
                uow.connection, resource_type="tool", state="PUBLISHED"
            )
            tools = await resources("tools", context.scope).get_many(
                uow.connection, [v["resource_id"] for v in versions]
            )
            blocked = await DeletionGuard(context.scope).blocked_refs(
                uow, [ContentRef("version", v["id"]) for v in versions]
            )
        permissions = {
            identifier: policy.actions("tool", identifier, resource_state(context, "tool", row))
            for identifier, row in tools.items()
        }
        versions = [
            v
            for v in versions
            if {"tool:manage", "version:read"} <= permissions.get(v["resource_id"], frozenset())
            and ContentRef("version", v["id"]) not in blocked
        ]
        reasons = await self.tools.binding_reasons(context, versions)
        result = []
        for row in versions:
            tool = tools[row["resource_id"]]
            allowed = permissions[tool["id"]]
            version = await self.tools.version_display(context, tool, row, allowed, reasons)
            executable = {"run:create", *version.definition.required_scopes} <= allowed
            result.append(
                SkillToolOption(
                    version_id=row["id"],
                    tool_code=tool["tool_code"],
                    name=tool["name"],
                    version_label=row["version_label"],
                    source_type=tool["source_type"],
                    input_schema=version.definition.input_schema,
                    output_schema=version.definition.output_schema,
                    available=version.execution_enabled and executable,
                    reason=version.unavailable_reason if executable else "当前成员未获工具调用权限",
                )
            )
        return result

    async def agent_options(self, context: AuthContext) -> list[SkillAgentOption]:
        from creativity_service.modules.agents.repositories import repository as resources

        policy = await self.authorization.read_policy(context)
        async with self.engine.connect() as connection:
            versions = await repository("resource_versions", context.scope).find(
                connection, resource_type="agent"
            )
            agents = await resources("agents", context.scope).get_many(
                connection, [v["resource_id"] for v in versions]
            )
        return [
            SkillAgentOption(agent_id=identifier, name=row["name"])
            for identifier, row in sorted(agents.items())
            if "agent:manage"
            in policy.actions("agent", identifier, resource_state(context, "agent", row))
        ]
