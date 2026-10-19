"""智能体只有一份可编辑配置；修订用于并发检查，运行快照保留历史内容。"""

from typing import Any

from creativity_service.core.context import AuthContext
from creativity_service.core.database import UnitOfWork, transaction
from creativity_service.core.deletion import ContentRef, DeletionGuard
from creativity_service.core.locking import ResourceKey
from creativity_service.core.observability.audit import append_audit
from creativity_service.core.primitives import ServiceError, digest, new_id
from creativity_service.modules.agents.access import locked_policy, locked_require
from creativity_service.modules.agents.base import ENVIRONMENTS
from creativity_service.modules.agents.deletion import (
    deletion_action,
    deletion_blocker,
    deletion_checks,
)
from creativity_service.modules.agents.registry import legacy_templates, templates
from creativity_service.modules.agents.repositories import RESOURCE_TABLES, repository, required
from creativity_service.modules.agents.schemas import (
    AgentConfigurationEdit,
    AgentConfigurationView,
    AgentCreate,
    AgentDefinition,
    AgentDeleteInput,
    AgentDetail,
    AgentEdit,
    AgentList,
    AgentOptions,
)
from creativity_service.modules.agents.scripts import environment_options, script_issues
from creativity_service.modules.agents.snapshots import AgentSnapshots
from creativity_service.modules.iam.reading import require_action, resource_state, visible_actions
from creativity_service.modules.integrations.repositories import configuration_key
from creativity_service.modules.memory.repositories import policy_key as memory_key


class AgentService(AgentSnapshots):
    async def add_configuration(
        self,
        uow: UnitOfWork,
        context: AuthContext,
        agent_id: str,
        definition: AgentDefinition,
    ) -> dict[str, Any]:
        version_id = agent_id
        repo = repository("resource_versions", context.scope)
        problems = script_issues(definition, self.sandbox, context.scope.channel_id)
        if problems:
            raise ServiceError(problems[0].code, problems[0].message, 422)
        from creativity_service.modules.resources.configuration import require_dependencies

        await require_dependencies(uow, context.scope, definition.dependency_ids())
        content = definition.model_dump(mode="json")
        row = await repo.add(
            uow,
            version_id,
            {
                "resource_type": "agent",
                "resource_id": agent_id,
                "version_label": "当前配置",
                "state": "CURRENT",
                "content": content,
                "content_digest": digest(
                    {"content": content, "output_schema": definition.output_schema}
                ),
                "dependencies": sorted(definition.dependency_ids()),
                "dependencies_digest": digest(sorted(definition.dependency_ids())),
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
        agent_id, audit_id = new_id("agent"), new_id("audit")
        version_id = agent_id
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
            if await repository("agents", context.scope, include_deleted=True).find(
                uow.connection, agent_code=body.agent_code
            ):
                raise ServiceError("AGENT_CODE_CONFLICT", "智能体编码已存在", 409)
            await repo.add(
                uow,
                agent_id,
                {**body.model_dump(exclude={"definition"}), "status": "ACTIVE"},
            )
            await self.add_configuration(uow, context, agent_id, body.definition)
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

    async def delete(self, context: AuthContext, agent_id: str, body: AgentDeleteInput) -> None:
        await self.require(context, "agent:manage", agent_id)
        audit_id = new_id("audit")
        async with transaction(
            self.engine, context.scope, self.keys(context, agent_id, ("audit_events", audit_id))
        ) as uow:
            policy = await locked_policy(uow, context)
            policy.require(context, "agent:manage", "agent", agent_id)
            repo = repository("agents", context.scope)
            agent = await repo.get(uow.connection, agent_id)
            if agent is None:
                raise ServiceError("NOT_FOUND", "智能体不存在或已删除", 404)
            if agent["revision"] != body.revision:
                raise ServiceError("REVISION_CONFLICT", "智能体已变更，请刷新后重试", 409)
            check = (await deletion_checks(uow.connection, context.scope, [agent]))[agent_id]
            blocker = deletion_blocker(policy, agent_id, check)
            if blocker:
                code, message = blocker
                raise ServiceError(code, message, 403 if code == "FORBIDDEN" else 409)
            await DeletionGuard(context.scope).check(uow, [ContentRef("agent", agent_id)])
            await repo.change(uow, agent_id, body.revision, {"is_deleted": True})
            await append_audit(uow, context, audit_id, "agent.delete", "agent", agent_id)

    async def edit_configuration(
        self, context: AuthContext, version_id: str, body: AgentConfigurationEdit
    ) -> AgentConfigurationView:
        agent, _ = await self.raw(context, version_id)
        await self.require(context, "agent:manage", agent["id"])
        audit_id = new_id("audit")
        keys = self.keys(
            context, agent["id"], ("resource_versions", version_id), ("audit_events", audit_id)
        )
        keys += self.configuration_edit_keys(context)
        keys += await self.dependency_keys(context, body.definition)
        async with transaction(self.engine, context.scope, keys) as uow:
            _, version = await self.locked_version(
                uow, context, agent["id"], version_id, body.revision, "agent:manage"
            )
            environments = await self.require_configuration_edit(uow, context, agent["id"])
            from creativity_service.modules.resources.configuration import require_dependencies

            await require_dependencies(uow, context.scope, body.definition.dependency_ids())
            problems = script_issues(body.definition, self.sandbox, context.scope.channel_id)
            if problems:
                raise ServiceError(problems[0].code, problems[0].message, 422)
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
                    "dependencies": sorted(body.definition.dependency_ids()),
                    "dependencies_digest": digest(sorted(body.definition.dependency_ids())),
                },
            )
            await self.validate_online_configuration(uow, context, agent, row, environments)
            await append_audit(
                uow,
                context,
                audit_id,
                "agent.configuration.edit",
                "agent",
                agent["id"],
                {"revision": row["revision"]},
            )
        return await self.configuration_view(context, row)

    async def list_agents(
        self, context: AuthContext, search: str | None = None, *, online_only: bool = False
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
            deletion = (
                await deletion_checks(connection, context.scope, rows) if not online_only else {}
            )
            if online_only:
                rows = [
                    row
                    for row in rows
                    if row["status"] == "ACTIVE"
                    and states.get(self.mapping_id(context, row["id"]), {}).get("is_online")
                    and states.get(self.mapping_id(context, row["id"]), {}).get("status")
                    == "ACTIVE"
                ]
        result = []
        for row in rows:
            allowed = policy.actions("agent", row["id"], resource_state(context, "agent", row))
            if "agent:manage" in allowed and (not online_only or "run:create" in allowed):
                actions = (
                    []
                    if online_only
                    else visible_actions(allowed, [("assist", "智能修改", "agent:manage")])
                )
                if not online_only:
                    actions.append(deletion_action(policy, row["id"], deletion[row["id"]]))
                result.append(
                    self.agent_summary(
                        row,
                        states.get(self.mapping_id(context, row["id"])),
                        actions,
                    )
                )
        allowed_new = policy.actions("agent", "new")
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
            configuration = await required(
                uow.connection, context.scope, "resource_versions", agent_id
            )
            await DeletionGuard(context.scope).check(uow, [ContentRef("version", agent_id)])
            deletion = (await deletion_checks(uow.connection, context.scope, [agent]))[agent_id]
        view = await self.agent_view(context, agent, permissions)
        view.actions.append(deletion_action(policy, agent_id, deletion))
        return AgentDetail(
            agent=view,
            configuration=await self.configuration_view(context, configuration, permissions),
        )

    async def configuration(self, context: AuthContext, agent_id: str) -> AgentConfigurationView:
        agent, row = await self.raw(context, agent_id)
        policy = await self.authorization.read_policy(context)
        permissions = policy.actions("agent", agent_id, resource_state(context, "agent", agent))
        require_action(permissions, "agent:manage")
        return await self.configuration_view(context, row, permissions)

    async def require_configuration_edit(
        self, uow: UnitOfWork, context: AuthContext, agent_id: str
    ) -> list[str]:
        """直接保存会改变全部已上线环境的新任务；在同一锁内复核这些环境的操作权限。"""
        from sqlalchemy import select

        from creativity_service.core.database.soft_delete import active_rows
        from creativity_service.modules.iam.authorization import action_allowed, effective_actions

        policy = await locked_policy(uow, context)
        table = repository("agent_environment_states", context.scope).table
        environments = list(
            await uow.connection.scalars(
                active_rows(
                    select(table.c.environment).where(
                        table.c.channel_id == context.scope.channel_id,
                        table.c.agent_id == agent_id,
                        table.c.is_online.is_(True),
                    )
                )
            )
        )
        for environment in environments:
            if policy.member is None or not action_allowed(
                effective_actions(
                    policy.member, list(policy.grants), environment, "agent", agent_id
                ),
                "release:publish",
            ):
                raise ServiceError("FORBIDDEN", "修改配置需要该智能体全部上线环境的操作权限", 403)
        return environments

    @staticmethod
    def configuration_edit_keys(context: AuthContext) -> list[ResourceKey]:
        """提前锁住固定环境集合，避免保存期间有环境上线却未被校验。"""
        return [
            key
            for environment in ENVIRONMENTS
            for scope in [context.scope.model_copy(update={"environment": environment})]
            for key in (configuration_key(scope), memory_key(scope))
        ]

    async def validate_online_configuration(
        self,
        uow: UnitOfWork,
        context: AuthContext,
        agent: dict[str, Any],
        configuration: dict[str, Any],
        environments: list[str],
    ) -> None:
        """单份配置覆盖全部上线环境，在原事务中逐个复核并原子回滚失败修改。"""
        for environment in environments:
            scoped = context.model_copy(
                update={"scope": context.scope.model_copy(update={"environment": environment})}
            )
            scoped_uow = UnitOfWork(uow.connection, scoped.scope, uow.keys)
            check, _, _ = await self.inspect_in(
                scoped_uow, scoped, agent, configuration, "production", require_evaluation=False
            )
            self.require_valid(check)

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
            python_environments=environment_options(self.sandbox, context.scope.channel_id),
            templates=templates(),
            legacy_templates=legacy_templates(),
            dependencies=result,
            environment=context.scope.environment,
            environment_label=ENVIRONMENTS[context.scope.environment],
        )
