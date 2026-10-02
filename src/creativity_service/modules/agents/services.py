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

    async def list_agents(self, context: AuthContext, search: str | None = None) -> AgentList:
        await self.authorization.authentication.revalidate(context)
        async with self.engine.connect() as connection:
            rows = await repository("agents", context.scope).find(connection)
        result = []
        for row in rows:
            if search and search.casefold() not in (row["name"] + row["description"]).casefold():
                continue
            if (
                await self.authorization.check(context, "agent:manage", "agent", row["id"])
            ).allowed:
                result.append(await self.agent_view(context, row))
        return AgentList(
            items=result,
            actions=await self.actions(context, "new", [("create", "新增智能体", "agent:manage")]),
        )

    async def detail(self, context: AuthContext, agent_id: str) -> AgentDetail:
        await self.require(context, "agent:manage", agent_id)
        async with transaction(self.engine, context.scope, self.keys(context, agent_id)) as uow:
            await DeletionGuard(context.scope).check(uow, [ContentRef("agent", agent_id)])
            agent = await required(uow.connection, context.scope, "agents", agent_id)
            versions = await repository("resource_versions", context.scope).find(
                uow.connection, resource_type="agent", resource_id=agent_id
            )
            visible_versions = []
            for version in versions:
                try:
                    await DeletionGuard(context.scope).check(
                        uow, [ContentRef("version", version["id"])]
                    )
                except ServiceError as exc:
                    if exc.code != "CONTENT_DELETED":
                        raise
                else:
                    visible_versions.append(version)
            versions = visible_versions
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
            agent=await self.agent_view(context, agent),
            versions=[await self.version_view(context, v) for v in versions],
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

    async def options(self, context: AuthContext) -> AgentOptions:
        await self.authorization.authentication.revalidate(context)
        async with self.engine.connect() as connection:
            rows = await repository("resource_versions", context.scope).find(connection)
        result = []
        for row in rows:
            kind = row["resource_type"]
            if kind not in {"prompt", "model_route", "tool", "skill"} or row["state"] not in {
                "DRAFT",
                "PUBLISHED",
            }:
                continue
            allowed = await self.authorization.check(
                context, "run:create", kind, row["resource_id"]
            )
            if not allowed.allowed:
                continue
            try:
                async with transaction(
                    self.engine, context.scope, self.keys(context, "options")
                ) as uow:
                    await DeletionGuard(context.scope).check(
                        uow,
                        [ContentRef(kind, row["resource_id"]), ContentRef("version", row["id"])],
                    )
                    resource = await required(
                        uow.connection, context.scope, RESOURCE_TABLES[kind], row["resource_id"]
                    )
                    if resource.get("status", "ACTIVE") == "ACTIVE":
                        result.append(await self.dependency_view(uow, context, row))
            except ServiceError as exc:
                if exc.code not in {"CONTENT_DELETED", "DEPENDENCY_INVALID"}:
                    raise
        return AgentOptions(
            templates=templates(),
            legacy_templates=legacy_templates(),
            dependencies=result,
            environment=context.scope.environment,
            environment_label=ENVIRONMENTS[context.scope.environment],
        )
