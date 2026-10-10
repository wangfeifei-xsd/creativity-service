"""智能体资源、乐观修订、版本查询、差异与依赖选择。"""

from typing import Any

from creativity_service.core.context import AuthContext
from creativity_service.core.database import UnitOfWork, transaction
from creativity_service.core.deletion import ContentRef, DeletionGuard
from creativity_service.core.locking import ResourceKey
from creativity_service.core.observability.audit import append_audit
from creativity_service.core.primitives import ServiceError, digest, new_id
from creativity_service.modules.agents.access import locked_require
from creativity_service.modules.agents.base import ENVIRONMENTS
from creativity_service.modules.agents.registry import legacy_templates, templates
from creativity_service.modules.agents.repositories import RESOURCE_TABLES, repository, required
from creativity_service.modules.agents.schemas import (
    AgentCreate,
    AgentDefinition,
    AgentDetail,
    AgentDifference,
    AgentEdit,
    AgentList,
    AgentOptions,
    AgentReleaseView,
    AgentVersionCreate,
    AgentVersionEdit,
    AgentVersionView,
)
from creativity_service.modules.agents.snapshots import AgentSnapshots
from creativity_service.modules.iam.reading import require_action, resource_state, visible_actions


class AgentService(AgentSnapshots):
    async def add_draft(
        self,
        uow: UnitOfWork,
        context: AuthContext,
        agent_id: str,
        version_id: str,
        label: str,
        definition: AgentDefinition,
    ) -> dict[str, Any]:
        repo = repository("resource_versions", context.scope)
        if await repo.find(
            uow.connection, resource_type="agent", resource_id=agent_id, version_label=label
        ):
            raise ServiceError("VERSION_LABEL_CONFLICT", "版本名称已存在", 409)
        from creativity_service.modules.resources.configuration import require_dependencies

        await require_dependencies(uow, context.scope, definition.bindings.ids())
        content = definition.model_dump(mode="json")
        row = await repo.add(
            uow,
            version_id,
            {
                "resource_type": "agent",
                "resource_id": agent_id,
                "version_label": label,
                "state": "DRAFT",
                "content": content,
                "content_digest": digest(
                    {"content": content, "output_schema": definition.output_schema}
                ),
                "dependencies": sorted(definition.bindings.ids()),
                "dependencies_digest": digest(sorted(definition.bindings.ids())),
                "output_schema": definition.output_schema,
                "created_by": context.principal_id,
            },
        )
        await DeletionGuard(context.scope).link(
            uow,
            digest([version_id, "agent", agent_id]),
            ContentRef("agent", agent_id),
            ContentRef("version", version_id),
        )
        return row

    async def create(self, context: AuthContext, body: AgentCreate) -> AgentDetail:
        from creativity_service.modules.agents.builtin import BUILTIN_CODE

        if body.agent_code == BUILTIN_CODE:
            raise ServiceError("BUILTIN_IMMUTABLE", "内置智能体由平台维护，不能创建或修改", 403)
        await self.require(context, "agent:manage", "new")
        agent_id, version_id, audit_id = new_id("agent"), new_id("version"), new_id("audit")
        keys = self.keys(
            context,
            agent_id,
            ("resource_versions", version_id),
            ("audit_events", audit_id),
            ("source_links", digest([version_id, "agent", agent_id])),
        )
        keys.append(ResourceKey(context.scope.channel_id, "agent-code", (body.agent_code,)))
        async with transaction(self.engine, context.scope, keys) as uow:
            await locked_require(uow, context, "agent:manage", "agent", "new")
            repo = repository("agents", context.scope)
            if await repo.find(uow.connection, agent_code=body.agent_code):
                raise ServiceError("AGENT_CODE_CONFLICT", "智能体编码已存在", 409)
            await repo.add(
                uow,
                agent_id,
                {**body.model_dump(exclude={"definition", "version_label"}), "status": "ACTIVE"},
            )
            await self.add_draft(
                uow, context, agent_id, version_id, body.version_label, body.definition
            )
            await append_audit(uow, context, audit_id, "agent.create", "agent", agent_id)
        return await self.detail(context, agent_id)

    async def edit(self, context: AuthContext, agent_id: str, body: AgentEdit) -> AgentDetail:
        await self.require(context, "agent:manage", agent_id)
        audit_id = new_id("audit")
        async with transaction(
            self.engine, context.scope, self.keys(context, agent_id, ("audit_events", audit_id))
        ) as uow:
            await locked_require(uow, context, "agent:manage", "agent", agent_id)
            await repository("agents", context.scope).change(
                uow, agent_id, body.revision, body.model_dump(exclude={"revision"})
            )
            await append_audit(uow, context, audit_id, "agent.edit", "agent", agent_id)
        return await self.detail(context, agent_id)

    async def create_version(
        self, context: AuthContext, agent_id: str, body: AgentVersionCreate
    ) -> AgentVersionView:
        await self.require(context, "agent:manage", agent_id)
        _, base = await self.raw(context, body.base_version_id)
        version_id, audit_id = new_id("version"), new_id("audit")
        keys = self.keys(
            context,
            agent_id,
            ("resource_versions", version_id),
            ("resource_versions", base["id"]),
            ("source_links", digest([version_id, "agent", agent_id])),
            ("audit_events", audit_id),
        )
        async with transaction(self.engine, context.scope, keys) as uow:
            _, base = await self.locked_version(
                uow, context, agent_id, base["id"], base["revision"], "agent:manage"
            )
            row = await self.add_draft(
                uow,
                context,
                agent_id,
                version_id,
                body.version_label,
                AgentDefinition.model_validate(base["content"]),
            )
            await append_audit(
                uow,
                context,
                audit_id,
                "agent.version.create",
                "agent",
                agent_id,
                {"version_id": version_id},
            )
        return await self.version_view(context, row)

    async def edit_version(
        self, context: AuthContext, version_id: str, body: AgentVersionEdit
    ) -> AgentVersionView:
        agent, _ = await self.raw(context, version_id)
        await self.require(context, "agent:manage", agent["id"])
        audit_id = new_id("audit")
        keys = self.keys(
            context, agent["id"], ("resource_versions", version_id), ("audit_events", audit_id)
        )
        async with transaction(self.engine, context.scope, keys) as uow:
            _, version = await self.locked_version(
                uow, context, agent["id"], version_id, body.revision, "agent:manage"
            )
            if version["state"] != "DRAFT":
                raise ServiceError("VERSION_FROZEN", "已发布版本不可修改，请新增草稿", 409)
            from creativity_service.modules.resources.configuration import require_dependencies

            await require_dependencies(uow, context.scope, body.definition.bindings.ids())
            content = body.definition.model_dump(mode="json")
            row = await repository("resource_versions", context.scope).change(
                uow,
                version_id,
                body.revision,
                {
                    "content": content,
                    "output_schema": body.definition.output_schema,
                    "content_digest": digest(
                        {"content": content, "output_schema": body.definition.output_schema}
                    ),
                    "dependencies": sorted(body.definition.bindings.ids()),
                    "dependencies_digest": digest(sorted(body.definition.bindings.ids())),
                },
            )
            await append_audit(
                uow,
                context,
                audit_id,
                "agent.version.edit",
                "agent",
                agent["id"],
                {"revision": row["revision"]},
            )
        return await self.version_view(context, row)

    async def list_agents(
        self, context: AuthContext, search: str | None = None, *, published_only: bool = False
    ) -> AgentList:
        policy = await self.authorization.read_policy(context)
        async with self.engine.connect() as connection:
            rows = await repository("agents", context.scope).find(connection)
            if search:
                rows = [
                    row
                    for row in rows
                    if search.casefold() in (row["name"] + row["description"]).casefold()
                ]
            states = await repository("agent_environment_states", context.scope).get_many(
                connection, [self.mapping_id(context, row["id"]) for row in rows]
            )
            if published_only:
                mappings = await repository("release_mappings", context.scope).get_many(
                    connection, [self.mapping_id(context, row["id"]) for row in rows]
                )
                versions = await repository("resource_versions", context.scope).get_many(
                    connection, [mapping["version_id"] for mapping in mappings.values()]
                )
                published_ids = {
                    mapping["resource_id"]
                    for mapping in mappings.values()
                    if versions.get(mapping["version_id"], {}).get("state") == "PUBLISHED"
                }
                rows = [
                    row
                    for row in rows
                    if row["id"] in published_ids
                    and row["status"] == "ACTIVE"
                    and states.get(self.mapping_id(context, row["id"]), {}).get("status", "ACTIVE")
                    == "ACTIVE"
                ]
        result = []
        for row in rows:
            allowed = policy.actions("agent", row["id"], resource_state(context, "agent", row))
            if "agent:manage" in allowed and (not published_only or "run:create" in allowed):
                result.append(
                    self.agent_summary(row, states.get(self.mapping_id(context, row["id"])), [])
                )
        from creativity_service.modules.agents.builtin import builtin_view

        allowed_new = policy.actions("agent", "new")
        assistant = builtin_view()
        if (
            not published_only
            and "agent:manage" in allowed_new
            and (
                not search
                or search.casefold() in (assistant.name + assistant.description).casefold()
            )
        ):
            result.insert(0, assistant)
        return AgentList(
            items=result,
            actions=visible_actions(
                allowed_new,
                [("create", "新增智能体", "agent:manage"), ("assist", "智能协助", "agent:manage")],
            ),
        )

    async def detail(self, context: AuthContext, agent_id: str) -> AgentDetail:
        policy = await self.authorization.read_policy(context)
        async with transaction(self.engine, context.scope, self.keys(context, agent_id)) as uow:
            agent = await required(uow.connection, context.scope, "agents", agent_id)
            permissions = policy.actions("agent", agent_id, resource_state(context, "agent", agent))
            require_action(permissions, "agent:manage")
            await DeletionGuard(context.scope).check(uow, [ContentRef("agent", agent_id)])
            versions = await repository("resource_versions", context.scope).find(
                uow.connection, resource_type="agent", resource_id=agent_id
            )
            blocked = await DeletionGuard(context.scope).blocked_refs(
                uow, [ContentRef("version", version["id"]) for version in versions]
            )
            versions = [v for v in versions if ContentRef("version", v["id"]) not in blocked]
            mapping = await repository("release_mappings", context.scope).get(
                uow.connection, self.mapping_id(context, agent_id)
            )
            records = await repository("agent_release_records", context.scope).find(
                uow.connection, agent_id=agent_id
            )
        versions.sort(key=lambda v: (v["created_at"], v["id"]))
        released = next((v for v in versions if mapping and v["id"] == mapping["version_id"]), None)
        draft = next((v for v in reversed(versions) if v["state"] == "DRAFT"), None)
        labels = {
            "instructions": "任务指令",
            "input_schema": "输入结构",
            "output_schema": "输出结构",
            "workflow_type": "流程类型",
            "entrypoint": "执行入口",
            "start_step": "起始步骤",
            "steps": "步骤配置",
            "edges": "流转条件",
            "bindings": "资源依赖",
            "limits": "运行限制",
            "context": "会话与记忆",
        }
        differences = [
            AgentDifference(
                field=k,
                label=label,
                before=released["content"].get(k) if released else None,
                after=draft["content"].get(k),
            )
            for k, label in labels.items()
            if draft and (not released or released["content"].get(k) != draft["content"].get(k))
        ]
        return AgentDetail(
            agent=await self.agent_view(context, agent, permissions),
            versions=[await self.version_view(context, v, permissions) for v in versions],
            release_version_id=mapping["version_id"] if mapping else None,
            release_revision=mapping["revision"] if mapping else None,
            differences=differences,
            releases=[
                AgentReleaseView(
                    release_id=r["id"],
                    environment=r["environment"],
                    environment_label=ENVIRONMENTS[r["environment"]],
                    version_id=r["version_id"],
                    version_label=r["version_label"],
                    operation=r["operation"],
                    operation_label={"publish": "发布", "rollback": "回滚"}[r["operation"]],
                    note=r["note"],
                    actor_name=r["actor_name"],
                    created_at=r["created_at"],
                    evidence_refs=r["evidence_refs"],
                )
                for r in sorted(records, key=lambda r: r["created_at"], reverse=True)
            ],
        )

    async def version(self, context: AuthContext, version_id: str) -> AgentVersionView:
        agent, row = await self.raw(context, version_id)
        policy = await self.authorization.read_policy(context)
        permissions = policy.actions("agent", agent["id"], resource_state(context, "agent", agent))
        require_action(permissions, "agent:manage")
        return await self.version_view(context, row, permissions)

    async def options(self, context: AuthContext) -> AgentOptions:
        policy = await self.authorization.read_policy(context)
        result = []
        async with transaction(self.engine, context.scope, self.keys(context, "options")) as uow:
            rows = await repository("resource_versions", context.scope).find_many(
                uow.connection, "resource_type", ["prompt", "model_route", "tool", "skill"]
            )
            releases = await repository("release_mappings", context.scope).find_many(
                uow.connection, "resource_type", ["prompt", "model_route", "tool", "skill"]
            )
            released_ids = {row["version_id"] for row in releases}
            rows = [
                row
                for row in rows
                if row["state"] == "PUBLISHED"
                and row["id"] == row["resource_id"]
                and row["id"] in released_ids
            ]
            resources = {}
            for kind in {row["resource_type"] for row in rows}:
                resources[kind] = await repository(RESOURCE_TABLES[kind], context.scope).get_many(
                    uow.connection, [r["resource_id"] for r in rows if r["resource_type"] == kind]
                )
            allowed_resources = {
                (kind, identifier)
                for kind, parents in resources.items()
                for identifier, parent in parents.items()
                if parent.get("status", "ACTIVE") == "ACTIVE"
                and "run:create"
                in policy.actions(kind, identifier, resource_state(context, kind, parent))
            }
            rows = [r for r in rows if (r["resource_type"], r["resource_id"]) in allowed_resources]
            refs = [ContentRef(kind, identifier) for kind, identifier in allowed_resources]
            refs.extend(ContentRef("version", row["id"]) for row in rows)
            blocked = await DeletionGuard(context.scope).blocked_refs(uow, refs)
            for row in rows:
                kind = row["resource_type"]
                if not (
                    {ContentRef(kind, row["resource_id"]), ContentRef("version", row["id"])}
                    & blocked
                ):
                    result.append(self.dependency_summary(row, resources[kind][row["resource_id"]]))
        return AgentOptions(
            templates=templates(),
            legacy_templates=legacy_templates(),
            dependencies=result,
            environment=context.scope.environment,
            environment_label=ENVIRONMENTS[context.scope.environment],
        )
