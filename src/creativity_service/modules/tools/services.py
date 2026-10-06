"""工具配置、版本发布、影响预览和管理调试服务。"""

from collections.abc import Awaitable, Callable
from typing import Any, Literal

from sqlalchemy.ext.asyncio import AsyncEngine

from creativity_service.core.context import AuthContext
from creativity_service.core.contracts import DisplayStatus, ResourceVersion, VisibleAction
from creativity_service.core.database import Repository, UnitOfWork, transaction
from creativity_service.core.database.tables import metadata as core_metadata
from creativity_service.core.deletion import ContentRef, DeletionGuard, content_key
from creativity_service.core.locking import ResourceKey, record_key
from creativity_service.core.observability.audit import append_audit
from creativity_service.core.primitives import ServiceError, digest, new_id, unavailable
from creativity_service.core.versioning import VersionService, version_view
from creativity_service.integrations.tools import EFFECT_LABELS, SOURCE_LABELS, AdapterRegistry
from creativity_service.modules.agents.access import locked_require
from creativity_service.modules.iam.authorization import IamAuthorization
from creativity_service.modules.iam.reading import require_action, resource_state, visible_actions
from creativity_service.modules.iam.repositories import policy_key
from creativity_service.modules.tools.ports import ToolDebugPort
from creativity_service.modules.tools.reading import ToolReadData
from creativity_service.modules.tools.repositories import ToolRepository
from creativity_service.modules.tools.schemas import (
    BindingOption,
    ExecutionOptions,
    ToolCallView,
    ToolCreate,
    ToolDefinition,
    ToolDetail,
    ToolEdit,
    ToolImpact,
    ToolList,
    ToolReference,
    ToolRelease,
    ToolTestDescription,
    ToolTestInput,
    ToolTestResult,
    ToolVersionCreate,
    ToolVersionEdit,
    ToolVersionView,
    ToolView,
)
from creativity_service.modules.tools.tables import metadata
from creativity_service.modules.tools.validation import validate_arguments, validate_definition


def status(value: str) -> DisplayStatus:
    names = {
        "ACTIVE": "已启用",
        "DISABLED": "已停用",
        "DRAFT": "未发布",
        "PUBLISHED": "已发布",
        "RETIRED": "已归档",
        "STARTED": "调用中",
        "SUCCEEDED": "成功",
        "FAILED": "失败",
        "DENIED": "已拒绝",
        "CACHED": "缓存命中",
    }
    return DisplayStatus(
        value=value,
        label=names.get(value, "状态待确认"),
        tone="success"
        if value in {"ACTIVE", "SUCCEEDED", "CACHED"}
        else "error"
        if value in {"FAILED", "DENIED"}
        else "default",
    )


class ToolVersionAuthorization:
    """公共版本动作按其工具资源授权，避免把版本编号误当独立资源授予权限。"""

    def __init__(self, service: "ToolService") -> None:
        self.service = service

    async def require(self, context: AuthContext, action: str, resource_id: str) -> None:
        async with self.service.engine.connect() as connection:
            row = await Repository(core_metadata.tables["resource_versions"], context.scope).get(
                connection, resource_id
            )
        tool_id = row["resource_id"] if row and row["resource_type"] == "tool" else resource_id
        await self.service.require(context, action, tool_id)


class ToolVersionValidator:
    def __init__(self, registry: AdapterRegistry) -> None:
        self.registry = registry

    async def validate(
        self,
        uow: UnitOfWork,
        context: AuthContext,
        version: ResourceVersion,
        operation: Literal["freeze", "release"],
    ) -> None:
        if version.resource_type != "tool":
            raise ServiceError("TOOL_INPUT_INVALID", "版本不属于工具", 422)
        row = await Repository(metadata.tables["tools"], context.scope).get(
            uow.connection, version.resource_id
        )
        if row is None or row["status"] != "ACTIVE":
            raise ServiceError("TOOL_UNAVAILABLE", "工具不存在或已停用", 409)
        definition = ToolDefinition.model_validate(version.content)
        validate_definition(definition)
        if context.scope.environment not in definition.environments:
            raise ServiceError("TOOL_FORBIDDEN", "版本未授权当前环境", 403)
        self.registry.validate(context.scope, definition, row["source_type"], executable=True)


class ToolService:
    def __init__(
        self,
        engine: AsyncEngine,
        authorization: IamAuthorization,
        registry: AdapterRegistry,
        runs: ToolDebugPort | None = None,
    ) -> None:
        self.engine, self.authorization, self.registry, self.runs = (
            engine,
            authorization,
            registry,
            runs,
        )
        self.repository = ToolRepository(engine)
        self.binding_checks = registry.binding_checks
        self.binding_read_checks: dict[
            Callable[[AuthContext, ToolDefinition], Awaitable[None]],
            Callable[[AuthContext, dict[str, ToolDefinition]], Awaitable[dict[str, str | None]]],
        ] = {}
        self.binding_option_providers: list[
            Callable[[AuthContext], Awaitable[list[BindingOption]]]
        ] = []
        self.versions = VersionService(
            engine, ToolVersionAuthorization(self), ToolVersionValidator(registry)
        )

    async def require(self, context: AuthContext, action: str, tool_id: str) -> None:
        await self.authorization.boundary(context, action, "tool", tool_id)

    async def actions(
        self,
        context: AuthContext,
        resource_id: str,
        keys: list[tuple[str, str, str]],
        permissions: frozenset[str] | None = None,
    ) -> list[VisibleAction]:
        if permissions is None:
            permissions = await self.authorization.allowed_actions(context, "tool", resource_id)
        return visible_actions(permissions, keys)

    async def create(self, context: AuthContext, body: ToolCreate) -> ToolView:
        await self.require(context, "tool:manage", "new")
        tool_id, audit_id = new_id("tool"), new_id("audit")
        scope = context.scope
        repo = Repository(metadata.tables["tools"], scope)
        keys = [
            content_key(scope),
            ResourceKey(scope.channel_id, "tool-code", (body.tool_code,)),
            record_key(scope.channel_id, "tools", tool_id),
            record_key(scope.channel_id, "audit_events", audit_id),
        ]
        async with transaction(self.engine, scope, keys) as uow:
            await DeletionGuard(scope).check(uow, [ContentRef("tool", tool_id)])
            if await repo.find(uow.connection, tool_code=body.tool_code):
                raise ServiceError("CODE_EXISTS", "工具编码已存在", 409)
            row = await repo.add(uow, tool_id, {**body.model_dump(), "status": "ACTIVE"})
            await append_audit(uow, context, audit_id, "tool.create", "tool", tool_id, {})
        return await self.view(context, row, [])

    async def edit(self, context: AuthContext, tool_id: str, body: ToolEdit) -> ToolView:
        await self.require(context, "tool:manage", tool_id)
        scope, audit_id = context.scope, new_id("audit")
        async with transaction(
            self.engine,
            scope,
            [
                policy_key(scope.channel_id),
                policy_key("system"),
                content_key(scope),
                record_key(scope.channel_id, "tools", tool_id),
                record_key(scope.channel_id, "audit_events", audit_id),
            ],
        ) as uow:
            await DeletionGuard(scope).check(uow, [ContentRef("tool", tool_id)])
            from creativity_service.modules.resources.configuration import require_edit

            await require_edit(uow, context, "tool", tool_id)
            row = await Repository(metadata.tables["tools"], scope).change(
                uow, tool_id, body.revision, body.model_dump(exclude={"revision"})
            )
            await append_audit(
                uow,
                context,
                audit_id,
                "tool.edit",
                "tool",
                tool_id,
                changed_fields=("name", "description", "owner"),
            )
        return await self.view(context, row, [])

    async def view(
        self,
        context: AuthContext,
        row: dict[str, Any],
        versions: list[dict[str, Any]],
        permissions: frozenset[str] | None = None,
    ) -> ToolView:
        effects = sorted({v["content"]["effect_type"] for v in versions if v["content"]})
        return ToolView(
            tool_id=row["id"],
            tool_code=row["tool_code"],
            name=row["name"],
            description=row["description"],
            source_type=row["source_type"],
            source_label=SOURCE_LABELS[row["source_type"]],
            owner=row["owner"],
            revision=row["revision"],
            status=status(row["status"]),
            effect_types=effects,
            effect_labels=[EFFECT_LABELS[e] for e in effects],
            actions=await self.actions(
                context,
                row["id"],
                [
                    ("edit", "编辑", "tool:manage"),
                ],
                permissions,
            ),
        )

    async def list_tools(
        self,
        context: AuthContext,
        source_type: str | None = None,
        state: str | None = None,
        effect_type: str | None = None,
        search: str | None = None,
        referenced_by: str | None = None,
    ) -> ToolList:
        policy = await self.authorization.read_policy(context)
        items = []
        agents: dict[str, ToolReference] = {}
        visible = []
        for row in await self.repository.rows(context, "tools"):
            if row["status"] == "DELETED":
                continue
            if (
                source_type
                and row["source_type"] != source_type
                or state
                and row["status"] != state
            ):
                continue
            if search and search.casefold() not in (row["name"] + row["description"]).casefold():
                continue
            if "tool:manage" not in policy.actions(
                "tool", row["id"], resource_state(context, "tool", row)
            ):
                continue
            visible.append(row)
        data = await ToolReadData.load(self.engine, context, [r["id"] for r in visible])
        for row in visible:
            versions = data.versions.get(row["id"], [])
            if effect_type and not any(
                v["content"].get("effect_type") == effect_type for v in versions
            ):
                continue
            impact = data.impacts[row["id"]]
            agents.update(
                {ref.version_id: ref for ref in impact.references if ref.resource_type == "agent"}
            )
            if referenced_by and not any(
                ref.version_id == referenced_by for ref in impact.references
            ):
                continue
            items.append(await self.view(context, row, versions, frozenset()))
        return ToolList(
            items=items,
            referenced_agents=list(agents.values()),
            actions=visible_actions(
                policy.actions("tool", "new"), [("create", "新增工具", "tool:manage")]
            ),
        )

    async def detail(self, context: AuthContext, tool_id: str) -> ToolDetail:
        policy = await self.authorization.read_policy(context)
        scope = context.scope
        async with transaction(self.engine, scope, [content_key(scope)]) as uow:
            row = await Repository(metadata.tables["tools"], scope).get(uow.connection, tool_id)
            if row is None or row["status"] == "DELETED":
                raise ServiceError("NOT_FOUND", "工具不存在", 404)
            permissions = policy.actions("tool", tool_id, resource_state(context, "tool", row))
            require_action(permissions, "tool:manage")
            versions = await Repository(core_metadata.tables["resource_versions"], scope).find(
                uow.connection, resource_type="tool", resource_id=tool_id, id=tool_id
            )
            mappings = await Repository(core_metadata.tables["release_mappings"], scope).find(
                uow.connection, resource_type="tool", resource_id=tool_id
            )
            await DeletionGuard(scope).check(
                uow,
                [
                    ContentRef("tool", tool_id),
                    *(ContentRef("version", v["id"]) for v in versions if v["state"] != "RETIRED"),
                ],
            )
        data = await ToolReadData.load(self.engine, context, [tool_id], target_versions=versions)
        reasons = await self.binding_reasons(
            context, [v for v in versions if v["state"] != "RETIRED"]
        )
        return ToolDetail(
            tool=await self.view(context, row, versions, permissions),
            versions=[
                await self.version_display(context, row, v, permissions, reasons)
                for v in versions
                if v["state"] != "RETIRED"
            ],
            release_version_id=mappings[0]["version_id"] if mappings else None,
            release_revision=mappings[0]["revision"] if mappings else None,
            impact=data.impacts[tool_id],
        )

    async def validate_config(self, context: AuthContext, definition: ToolDefinition) -> None:
        validate_definition(definition)
        # 首版配置只能授予当前已核准渠道环境，复制到其他环境须再次授权。
        if set(definition.environments) != {context.scope.environment}:
            raise ServiceError("TOOL_FORBIDDEN", "工具权限须使用当前已授权渠道环境", 403)

    @staticmethod
    def dependencies(definition: ToolDefinition) -> list[str]:
        return (
            [definition.binding.script.skill_version_id] if definition.binding.script else []
        ) + ([definition.write_policy.status_tool_version_id] if definition.write_policy else [])

    async def create_version(
        self, context: AuthContext, tool_id: str, body: ToolVersionCreate
    ) -> ToolVersionView:
        await self.require(context, "version:edit", tool_id)
        await self.validate_config(context, body.definition)
        async with self.engine.connect() as connection:
            tool = await Repository(metadata.tables["tools"], context.scope).get(
                connection, tool_id
            )
        if tool is None or tool["status"] != "ACTIVE":
            raise ServiceError("TOOL_UNAVAILABLE", "工具不存在或已停用", 409)
        self.registry.validate(
            context.scope, body.definition, tool["source_type"], executable=False
        )
        version = await self.versions.create_draft(
            context,
            "tool",
            tool_id,
            body.version_label,
            body.definition.model_dump(mode="json"),
            self.dependencies(body.definition),
            body.definition.output_schema,
        )
        return await self.version_detail(context, version.version_id)

    async def import_draft(
        self,
        context: AuthContext,
        tool: ToolCreate,
        version: ToolVersionCreate,
        *,
        uow: UnitOfWork | None = None,
        import_key: str | None = None,
        target_tool_id: str | None = None,
    ) -> ToolVersionView:
        """组合导入由调用方预先授权并持有全部锁，草稿与映射在同一事务提交。"""
        if uow is not None:
            if import_key is None:
                raise ValueError("组合导入须提供固定导入键")
            for key in self.import_keys(
                context, import_key, tool.tool_code, target_tool_id, version.version_label
            ):
                uow.require_lock(key)
            validate_definition(version.definition)
            self.registry.validate(
                context.scope, version.definition, tool.source_type, executable=False
            )
            tool_id, version_id, audit_id, source_id = self.import_ids(import_key)
            tool_id = target_tool_id or tool_id
            version_id = tool_id
            repo = Repository(metadata.tables["tools"], context.scope)
            existing = await repo.get(uow.connection, tool_id) if target_tool_id else None
            if target_tool_id and (
                existing is None
                or existing["status"] != "ACTIVE"
                or existing["source_type"] != tool.source_type
            ):
                raise ServiceError("TOOL_UNAVAILABLE", "目标工具不存在或已停用", 409)
            if not target_tool_id and await repo.find(uow.connection, tool_code=tool.tool_code):
                raise ServiceError("CODE_EXISTS", "工具编码已存在", 409)
            await DeletionGuard(context.scope).check(uow, [ContentRef("tool", tool_id)])
            if not target_tool_id:
                await repo.add(uow, tool_id, {**tool.model_dump(), "status": "ACTIVE"})
            content = version.definition.model_dump(mode="json")
            output = version.definition.output_schema
            versions = Repository(core_metadata.tables["resource_versions"], context.scope)
            if await versions.get(uow.connection, tool_id):
                raise ServiceError(
                    "CONFIGURATION_EXISTS", "工具已有配置，请直接修改或创建独立工具", 409
                )
            row = await Repository(core_metadata.tables["resource_versions"], context.scope).add(
                uow,
                version_id,
                {
                    "resource_type": "tool",
                    "resource_id": tool_id,
                    "version_label": "当前配置",
                    "state": "DRAFT",
                    "content": content,
                    "content_digest": digest({"content": content, "output_schema": output}),
                    "dependencies": [],
                    "dependencies_digest": digest([]),
                    "output_schema": output,
                    "created_by": context.principal_id,
                },
            )
            await DeletionGuard(context.scope).link(
                uow, source_id, ContentRef("tool", tool_id), ContentRef("version", version_id)
            )
            await append_audit(
                uow, context, audit_id, "tool.import", "tool", tool_id, {"version_id": version_id}
            )
            return ToolVersionView(
                version=version_view(row),
                revision=1,
                definition=version.definition,
                status=status("DRAFT"),
                execution_enabled=False,
                unavailable_reason="工具尚未发布",
                actions=[],
            )
        resource = await self.create(context, tool)
        return await self.create_version(context, resource.tool_id, version)

    @staticmethod
    def import_ids(import_key: str) -> tuple[str, str, str, str]:
        return (
            digest([import_key, "tool"]),
            digest([import_key, "tool"]),
            digest([import_key, "audit"]),
            digest([import_key, "source"]),
        )

    @classmethod
    def import_keys(
        cls,
        context: AuthContext,
        import_key: str,
        code: str,
        target_tool_id: str | None = None,
        version_label: str = "",
    ) -> list[ResourceKey]:
        tool_id, version_id, audit_id, source_id = cls.import_ids(import_key)
        tool_id = target_tool_id or tool_id
        version_id = tool_id
        scope = context.scope
        return [
            content_key(scope),
            ResourceKey(scope.channel_id, "tool-code", (code,)),
            ResourceKey(scope.channel_id, "version-label", ("tool", tool_id, version_label)),
            record_key(scope.channel_id, "tools", tool_id),
            record_key(scope.channel_id, "resource_versions", version_id),
            record_key(scope.channel_id, "audit_events", audit_id),
            record_key(scope.channel_id, "source_links", source_id),
        ]

    async def check_binding(self, context: AuthContext, definition: ToolDefinition) -> None:
        for check in self.binding_checks:
            await check(context, definition)

    async def binding_reasons(
        self, context: AuthContext, versions: list[dict[str, Any]]
    ) -> dict[str, str | None]:
        definitions = {r["id"]: ToolDefinition.model_validate(r["content"]) for r in versions}
        reasons: dict[str, str | None] = {}
        for check in self.binding_checks:
            if check in self.binding_read_checks:
                values = await self.binding_read_checks[check](context, definitions)
                for identifier, reason in values.items():
                    if reason:
                        reasons[identifier] = reason
            else:
                for identifier, definition in definitions.items():
                    try:
                        await check(context, definition)
                    except ServiceError as exc:
                        reasons[identifier] = exc.message
        return reasons

    async def edit_version(
        self, context: AuthContext, version_id: str, body: ToolVersionEdit
    ) -> ToolVersionView:
        tool, _ = await self.repository.resolve(context, version_id)
        await self.require(context, "version:edit", tool["id"])
        await self.validate_config(context, body.definition)
        self.registry.validate(
            context.scope, body.definition, tool["source_type"], executable=False
        )
        await self.versions.edit_draft(
            context,
            version_id,
            body.revision,
            body.definition.model_dump(mode="json"),
            self.dependencies(body.definition),
            body.definition.output_schema,
        )
        return await self.version_detail(context, version_id)

    async def version_detail(self, context: AuthContext, version_id: str) -> ToolVersionView:
        tool, row = await self.repository.resolve(context, version_id)
        policy = await self.authorization.read_policy(context)
        permissions = policy.actions("tool", tool["id"], resource_state(context, "tool", tool))
        return await self.version_display(context, tool, row, permissions)

    async def version_display(
        self,
        context: AuthContext,
        tool: dict[str, Any],
        row: dict[str, Any],
        permissions: frozenset[str],
        binding_reasons: dict[str, str | None] | None = None,
    ) -> ToolVersionView:
        require_action(permissions, "version:read")
        definition = ToolDefinition.model_validate(row["content"])
        reason = None
        try:
            self.registry.validate(context.scope, definition, tool["source_type"], executable=True)
            if binding_reasons is None:
                await self.check_binding(context, definition)
            else:
                reason = binding_reasons.get(row["id"])
            if tool["status"] != "ACTIVE":
                reason = "工具已停用"
        except ServiceError as exc:
            reason = exc.message
        keys = [("test", "测试", "run:create")]
        if row["state"] == "DRAFT" or (
            row["state"] == "PUBLISHED" and "release:publish" in permissions
        ):
            keys += [("edit", "修改", "version:edit")]
        return ToolVersionView(
            version=version_view(row),
            revision=row["revision"],
            definition=definition,
            status=status(row["state"]),
            execution_enabled=reason is None,
            unavailable_reason=reason,
            actions=visible_actions(permissions, keys),
        )

    async def freeze(self, context: AuthContext, version_id: str, revision: int) -> ToolVersionView:
        view = await self.version_detail(context, version_id)
        await self.check_binding(context, view.definition)
        await self.versions.freeze(context, version_id, revision)
        return await self.version_detail(context, version_id)

    async def release(self, context: AuthContext, tool_id: str, body: ToolRelease) -> ToolDetail:
        view = await self.version_detail(context, body.version_id)
        await self.check_binding(context, view.definition)
        tool, _ = await self.repository.resolve(context, body.version_id)
        if tool["id"] != tool_id:
            raise ServiceError("NOT_FOUND", "工具版本不存在", 404)
        await self.versions.release(context, body.version_id, body.expected_revision, body.note)
        return await self.detail(context, tool_id)

    async def check_dependency(self, context: AuthContext, version_id: str) -> ResourceVersion:
        """16/17 在发布与受理边界复核本地固定工具版本及实际可执行性。"""
        view = await self.version_detail(context, version_id)
        if view.version.state != "PUBLISHED" or not view.execution_enabled:
            raise ServiceError(
                "TOOL_UNAVAILABLE", view.unavailable_reason or "工具依赖须为已冻结版本", 409
            )
        return view.version

    async def impacts(self, context: AuthContext, tool_ids: list[str]) -> dict[str, ToolImpact]:
        policy = await self.authorization.read_policy(context)
        async with self.engine.connect() as connection:
            tools = await Repository(metadata.tables["tools"], context.scope).get_many(
                connection, tool_ids
            )
        for identifier in dict.fromkeys(tool_ids):
            row = tools.get(identifier)
            if row is None:
                raise ServiceError("NOT_FOUND", "工具不存在", 404)
            require_action(
                policy.actions("tool", identifier, resource_state(context, "tool", row)),
                "tool:manage",
            )
        return (await ToolReadData.load(self.engine, context, tool_ids)).impacts

    async def impact(self, context: AuthContext, tool_id: str) -> ToolImpact:
        return (await self.impacts(context, [tool_id]))[tool_id]

    async def disable(self, context: AuthContext, tool_id: str, revision: int) -> ToolDetail:
        return await self.set_enabled(context, tool_id, revision, False)

    async def enable(self, context: AuthContext, tool_id: str, revision: int) -> ToolDetail:
        return await self.set_enabled(context, tool_id, revision, True)

    async def set_enabled(
        self, context: AuthContext, tool_id: str, revision: int, enabled: bool
    ) -> ToolDetail:
        await self.require(context, "tool:manage", tool_id)
        scope, audit_id = context.scope, new_id("audit")
        async with transaction(
            self.engine,
            scope,
            [
                content_key(scope),
                policy_key("system"),
                policy_key(scope.channel_id),
                record_key(scope.channel_id, "tools", tool_id),
                record_key(scope.channel_id, "audit_events", audit_id),
            ],
        ) as uow:
            await locked_require(uow, context, "tool:manage", "tool", tool_id)
            await Repository(metadata.tables["tools"], scope).change(
                uow, tool_id, revision, {"status": "ACTIVE" if enabled else "DISABLED"}
            )
            # 恢复仅改变资源开关；发布映射、依赖绑定和执行时的当前权限校验均继续生效。
            await append_audit(
                uow,
                context,
                audit_id,
                "tool.enable" if enabled else "tool.disable",
                "tool",
                tool_id,
                {"state": "ACTIVE" if enabled else "DISABLED"},
                changed_fields=("status",),
            )
        return await self.detail(context, tool_id)

    async def execution_options(
        self, context: AuthContext, tool_id: str = "new"
    ) -> ExecutionOptions:
        from creativity_service.integrations.sandbox import ContainerSandbox
        from creativity_service.modules.agents.repositories import repository

        policy = await self.authorization.read_policy(context)
        if tool_id == "new":
            permissions = policy.actions("tool", "new")
        else:
            async with self.engine.connect() as connection:
                parent = await repository("tools", context.scope).get(connection, tool_id)
            permissions = policy.actions(
                "tool", tool_id, resource_state(context, "tool", parent) if parent else None
            )
        require_action(permissions, "tool:manage")
        profiles = [
            {
                "profile_id": p.profile_id,
                "name": p.name,
                "mode": p.mode,
                "digest": digest(p.model_dump(mode="json")),
                "endpoint": f"sandbox://{p.profile_id}/{digest(p.model_dump(mode='json'))}",
            }
            for p in ContainerSandbox().settings.profiles
            if context.scope.channel_id in p.channels
        ]
        queries, scripts = [], []
        async with transaction(self.engine, context.scope, [content_key(context.scope)]) as uow:
            versions = await repository("resource_versions", context.scope).find_many(
                uow.connection, "resource_type", ["tool", "skill"]
            )
            versions = [v for v in versions if v["state"] in {"DRAFT", "PUBLISHED"}]
            parents = {
                kind: await repository(table, context.scope).get_many(
                    uow.connection,
                    [v["resource_id"] for v in versions if v["resource_type"] == kind],
                )
                for kind, table in (("tool", "tools"), ("skill", "skills"))
            }
            blocked = await DeletionGuard(context.scope).blocked_refs(
                uow, [ContentRef("version", v["id"]) for v in versions]
            )
        for version in versions:
            kind = version["resource_type"]
            parent = parents[kind].get(version["resource_id"])
            if (
                not parent
                or parent["status"] != "ACTIVE"
                or ContentRef("version", version["id"]) in blocked
            ):
                continue
            if "run:create" not in policy.actions(
                kind, parent["id"], resource_state(context, kind, parent)
            ):
                continue
            item = {
                "version_id": version["id"],
                "name": parent["name"],
                "version_label": version["version_label"],
            }
            if kind == "tool" and version["content"].get("effect_type") == "READ_ONLY":
                queries.append(item)
            elif kind == "skill" and version["state"] == "PUBLISHED":
                scripts.append(
                    {
                        **item,
                        "paths": [
                            f["relative_path"]
                            for f in version["content"]["files"]
                            if f["relative_path"].endswith(".py")
                        ],
                    }
                )
        return ExecutionOptions(profiles=profiles, scripts=scripts, queries=queries)

    async def bindings(
        self, context: AuthContext, tool_id: str | None = None
    ) -> list[BindingOption]:
        await self.require(context, "tool:manage", tool_id or "new")
        options = self.registry.options(context.scope)
        for provider in self.binding_option_providers:
            options.extend(await provider(context))
        return options

    async def test_description(self, context: AuthContext, version_id: str) -> ToolTestDescription:
        version = await self.version_detail(context, version_id)
        return ToolTestDescription(
            version_id=version_id,
            revision=version.revision,
            input_schema=version.definition.input_schema,
            trusted_scope=context.scope,
            principal_name="当前已验证管理成员",
            executable=version.execution_enabled and self.runs is not None,
            unavailable_reason=version.unavailable_reason
            or ("工具测试暂不可用" if self.runs is None else None),
        )

    async def test(
        self, context: AuthContext, version_id: str, body: ToolTestInput
    ) -> ToolTestResult:
        tool, row = await self.repository.resolve(context, version_id)
        await self.require(context, "tool:manage", tool["id"])
        await self.require(context, "run:create", tool["id"])
        if row["revision"] != body.revision:
            raise ServiceError("REVISION_CONFLICT", "版本已变更，请刷新后测试", 409)
        if tool["status"] != "ACTIVE":
            raise ServiceError("TOOL_UNAVAILABLE", "工具已停用", 403)
        definition = ToolDefinition.model_validate(row["content"])
        self.registry.validate(context.scope, definition, tool["source_type"], executable=True)
        validate_arguments(body.arguments, definition)
        if self.runs is None:
            raise unavailable("工具调试运行与运行限额服务")
        return await self.runs.create_debug_run(context, version_id, body)

    async def calls(self, context: AuthContext, tool_id: str | None = None) -> list[ToolCallView]:
        await self.require(context, "run:read", tool_id or "*")
        rows = await self.repository.rows(
            context, "tool_calls", **({"tool_id": tool_id} if tool_id else {})
        )
        return [await self.call(context, row["id"]) for row in rows]

    async def call(self, context: AuthContext, call_id: str) -> ToolCallView:
        async with transaction(self.engine, context.scope, [content_key(context.scope)]) as uow:
            await DeletionGuard(context.scope).check(uow, [ContentRef("tool_call", call_id)])
            row = await Repository(metadata.tables["tool_calls"], context.scope).get(
                uow.connection, call_id
            )
        if row is None:
            raise ServiceError("NOT_FOUND", "调用记录不存在", 404)
        await self.require(context, "run:read", row["tool_id"])
        tool, version = await self.repository.resolve(context, row["tool_version_id"])
        return ToolCallView(
            tool_call_id=row["id"],
            tool_name=tool["name"],
            version_label=version["version_label"],
            run_id=row["run_id"],
            step_id=row["step_id"],
            attempt_id=row["attempt_id"],
            state=status(row["state"]),
            args_digest=row["args_digest"],
            redacted_arguments=row["redacted_arguments"],
            result_summary=row["result_summary"],
            source_request_id=row["source_request_id"],
            latency_ms=row["latency_ms"],
            error=row["error"],
            evidence_ids=row["evidence_ids"],
            created_at=row["created_at"],
        )
