"""资源与环境锁内重读草稿、依赖和评测证据，原子生成版本并切换映射。"""

from creativity_service.core.context import AuthContext
from creativity_service.core.database import transaction
from creativity_service.core.deletion import ContentRef, DeletionGuard
from creativity_service.core.locking import record_key
from creativity_service.core.observability.audit import append_audit
from creativity_service.core.primitives import ServiceError, digest, new_id
from creativity_service.modules.agents.access import locked_require
from creativity_service.modules.agents.repositories import dependency_rows, repository, required
from creativity_service.modules.agents.schemas import (
    AgentDefinition,
    AgentDetail,
    AgentReleaseInput,
    AgentStateInput,
)
from creativity_service.modules.agents.services import AgentService
from creativity_service.modules.iam.repositories import one


class ReleaseService:
    def __init__(self, agents: AgentService) -> None:
        self.agents = agents

    async def release(
        self, context: AuthContext, agent_id: str, body: AgentReleaseInput
    ) -> AgentDetail:
        s, scope = self.agents, context.scope
        if body.environment != scope.environment:
            raise ServiceError("FORBIDDEN", "请进入目标环境工作区后发布", 403)
        await s.require(context, "release:publish", agent_id)
        _, initial = await s.raw(context, body.version_id)
        definition = AgentDefinition.model_validate(initial["content"])
        mapping_id = s.mapping_id(context, agent_id)
        version_id = new_id("version") if initial["state"] == "DRAFT" else body.version_id
        release_id, audit_id = new_id("release"), new_id("audit")
        async with s.engine.connect() as connection:
            dependencies = await dependency_rows(connection, scope, definition.bindings.ids())
        dependency_ids = [d["id"] for d in dependencies]
        sources = [("agent", agent_id), *[("version", d) for d in dependency_ids]]
        keys = s.keys(
            context,
            agent_id,
            ("resource_versions", body.version_id),
            ("resource_versions", version_id),
            ("release_mappings", mapping_id),
            ("agent_release_records", release_id),
            ("audit_events", audit_id),
        )
        keys += [
            record_key(scope.channel_id, "source_links", digest([version_id, kind, identifier]))
            for kind, identifier in sources
        ]
        keys += [
            record_key(scope.channel_id, "resource_references", digest([version_id, d]))
            for d in dependency_ids
        ]
        keys += await s.dependency_keys(context, definition)
        if s.evaluation:
            keys += s.evaluation.keys(context, body.evaluation_refs)
        async with transaction(s.engine, scope, keys) as uow:
            agent, version = await s.locked_version(
                uow, context, agent_id, body.version_id, body.revision, "release:publish"
            )
            mappings = repository("release_mappings", scope)
            previous = await mappings.get(uow.connection, mapping_id)
            if (previous["revision"] if previous else None) != body.expected_mapping_revision:
                raise ServiceError("REVISION_CONFLICT", "环境映射已变化，请刷新发布记录", 409)
            if body.operation == "rollback":
                records = await repository("agent_release_records", scope).find(
                    uow.connection, agent_id=agent_id, version_id=version["id"]
                )
                if version["state"] != "PUBLISHED" or not records:
                    raise ServiceError("ROLLBACK_INVALID", "只能回滚到当前环境的历史发布版本", 422)
            elif version["state"] not in {"DRAFT", "PUBLISHED"}:
                raise ServiceError("VERSION_UNAVAILABLE", "此版本不可发布", 422)
            validation, resolved, _ = await s.inspect_in(
                uow, context, agent, version, "production", body.evaluation_refs
            )
            s.require_valid(validation)
            if [d["id"] for d in resolved] != dependency_ids:
                raise ServiceError("REVISION_CONFLICT", "依赖闭包已变化，请重新检查", 409)
            if (
                version["state"] == "PUBLISHED"
                and version["dependencies_digest"] != validation.dependencies_digest
            ):
                raise ServiceError(
                    "DEPENDENCY_INVALID", "历史版本的依赖或策略已变化，请创建新版本", 422
                )
            if version["state"] == "DRAFT":
                published = await repository("resource_versions", scope).find(
                    uow.connection, resource_type="agent", resource_id=agent_id, state="PUBLISHED"
                )
                label = f"发布版本 {len(published) + 1}"
                version = await repository("resource_versions", scope).add(
                    uow,
                    version_id,
                    {
                        "resource_type": "agent",
                        "resource_id": agent_id,
                        "version_label": label,
                        "state": "PUBLISHED",
                        "content": version["content"],
                        "content_digest": version["content_digest"],
                        "dependencies": dependency_ids,
                        "dependencies_digest": validation.dependencies_digest,
                        "output_schema": version["output_schema"],
                        "created_by": context.principal_id,
                    },
                )
                for kind, identifier in sources:
                    await DeletionGuard(scope).link(
                        uow,
                        digest([version_id, kind, identifier]),
                        ContentRef(kind, identifier),
                        ContentRef("version", version_id),
                    )
                for dep in resolved:
                    await repository("resource_references", scope).add(
                        uow,
                        digest([version_id, dep["id"]]),
                        {
                            "source_version_id": version_id,
                            "target_version_id": dep["id"],
                            "target_resource_type": dep["resource_type"],
                        },
                    )
            values = {
                "resource_type": "agent",
                "resource_id": agent_id,
                "version_id": version_id,
                "published_by": context.principal_id,
                "release_note": body.note,
            }
            if previous:
                await mappings.change(uow, mapping_id, previous["revision"], values)
            else:
                await mappings.add(uow, mapping_id, values)
            account = await one(uow.connection, "platform_accounts", "system", id=context.actor_id)
            await repository("agent_release_records", scope).add(
                uow,
                release_id,
                {
                    "agent_id": agent_id,
                    "version_id": version_id,
                    "version_label": version["version_label"],
                    "previous_version_id": previous["version_id"] if previous else None,
                    "source_revision": body.revision,
                    "operation": body.operation,
                    "note": body.note,
                    "actor_id": context.principal_id,
                    "actor_name": account["display_name"] if account else None,
                    "content_digest": validation.content_digest,
                    "dependencies_digest": validation.dependencies_digest,
                    "evidence_refs": list(body.evaluation_refs),
                    "checks": [c.model_dump(mode="json") for c in validation.checks],
                },
            )
            await append_audit(
                uow,
                context,
                audit_id,
                f"agent.{body.operation}",
                "agent",
                agent_id,
                {
                    "version_id": version_id,
                    "content_digest": validation.content_digest,
                },
            )
        return await s.detail(context, agent_id)

    async def state(
        self, context: AuthContext, agent_id: str, body: AgentStateInput
    ) -> AgentDetail:
        s, scope = self.agents, context.scope
        await s.require(context, "release:publish", agent_id)
        audit_id = new_id("audit")
        identifier = s.mapping_id(context, agent_id)
        async with transaction(
            s.engine,
            scope,
            s.keys(
                context,
                agent_id,
                ("audit_events", audit_id),
                ("agent_environment_states", identifier),
            ),
        ) as uow:
            await locked_require(uow, context, "release:publish", "agent", agent_id)
            await required(uow.connection, scope, "agents", agent_id)
            await repository("agents", scope).change(uow, agent_id, body.revision, {})
            repo = repository("agent_environment_states", scope)
            previous = await repo.get(uow.connection, identifier)
            values = {
                "agent_id": agent_id,
                "status": {
                    "offline": "OFFLINE",
                    "emergency_stop": "EMERGENCY_STOP",
                    "enable": "ACTIVE",
                }[body.operation],
                "reason": body.reason,
            }
            if previous:
                await repo.change(uow, identifier, previous["revision"], values)
            else:
                await repo.add(uow, identifier, values)
            await append_audit(
                uow,
                context,
                audit_id,
                f"agent.{body.operation}",
                "agent",
                agent_id,
                {"state": values["status"]},
            )
        return await s.detail(context, agent_id)
