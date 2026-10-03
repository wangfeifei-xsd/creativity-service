"""渠道角色动作上限实时参与授权，编辑同时核查所有受影响成员的潜在授权。"""

from typing import Any

from pydantic import Field

from creativity_service.core.auth.authentication import AdminSession
from creativity_service.core.context import AuthContext
from creativity_service.core.database import Repository, transaction
from creativity_service.core.locking import record_key
from creativity_service.core.primitives import Contract, Revision, ServiceError, new_id
from creativity_service.modules.iam.access import AccessService
from creativity_service.modules.iam.audit import append_event
from creativity_service.modules.iam.authorization import effective_actions
from creativity_service.modules.iam.operations_tables import metadata
from creativity_service.modules.iam.repositories import (
    membership_state,
    policy_key,
    role_catalog,
    rows,
)
from creativity_service.modules.iam.roles import ACTION_NAMES, PLATFORM_ACTIONS, ROLE_ACTIONS


class RoleSave(Contract):
    revision: Revision | None = None
    name: str = Field(min_length=1, max_length=128)
    allowed_actions: list[str] = Field(min_length=1, max_length=100)
    active: bool = True


class CustomRoles:
    def __init__(self, access: AccessService) -> None:
        self.access, self.engine = access, access.repository.engine

    async def context(self, session: AdminSession) -> AuthContext:
        if not isinstance(session.context, AuthContext):
            raise ServiceError("FORBIDDEN", "请先进入渠道工作区", 403)
        return await self.access.context(
            session, session.context.scope.channel_id, "membership:manage"
        )

    async def list(self, session: AdminSession) -> list[dict[str, Any]]:
        context = await self.context(session)
        async with self.engine.connect() as connection:
            catalog = await role_catalog(connection, context.scope.channel_id)
            members = await rows(connection, "channel_memberships", context.scope.channel_id)
        return [
            dict(
                id=code,
                name=value["name"],
                builtin=value["builtin"],
                revision=value["revision"],
                active=value["state"] == "ACTIVE",
                state_label="启用" if value["state"] == "ACTIVE" else "停用",
                allowed_actions=value["allowed_actions"],
                action_names=[ACTION_NAMES[a] for a in value["allowed_actions"]],
                member_count=sum(code in m["roles"] and m["status"] == "ACTIVE" for m in members),
            )
            for code, value in catalog.items()
        ]

    async def save(
        self, session: AdminSession, body: RoleSave, identifier: str | None = None
    ) -> dict[str, Any]:
        context = await self.context(session)
        known_pairs = await self.access.workspace_pairs(context)
        if identifier in ROLE_ACTIONS:
            raise ServiceError("BUILTIN_ROLE_READ_ONLY", "内置角色只读", 409)
        creating = identifier is None
        identifier = identifier or new_id("role")
        event_id = new_id("audit")
        requested = frozenset(body.allowed_actions)
        if (
            len(requested) != len(body.allowed_actions)
            or requested - ACTION_NAMES.keys()
            or requested & PLATFORM_ACTIONS
        ):
            raise ServiceError("ROLE_ACTIONS_INVALID", "角色动作无效、重复或包含平台治理权限", 422)
        async with transaction(
            self.engine,
            context.scope,
            [
                policy_key("system"),
                policy_key(context.scope.channel_id),
                record_key(context.scope.channel_id, "custom_roles", identifier),
                record_key(context.scope.channel_id, "audit_events", event_id),
            ],
        ) as uow:
            member, grants = await self.access.locked_policy(uow, session, "membership:manage")
            catalog = await role_catalog(uow.connection, context.scope.channel_id)
            old = catalog.get(identifier)
            if not old and not creating:
                raise ServiceError("NOT_FOUND", "当前渠道没有此角色", 404)
            if old and old["builtin"]:
                raise ServiceError("BUILTIN_ROLE_READ_ONLY", "内置角色只读", 409)
            if bool(old) != (body.revision is not None):
                raise ServiceError("REVISION_CONFLICT", "角色已变化，请刷新后重试", 409)
            allowed = effective_actions(
                member,
                grants,
                context.scope.environment,
                context.scope.data_scope_id or "",
                "channel",
                context.scope.channel_id,
            )
            if not requested <= allowed:
                raise ServiceError("GRANT_SCOPE_EXCEEDED", "角色权限不能超出当前可授权范围", 403)
            values = {
                "name": body.name,
                "allowed_actions": sorted(requested),
                "state": "ACTIVE" if body.active else "DISABLED",
            }
            for target in await rows(
                uow.connection, "channel_memberships", context.scope.channel_id
            ):
                if identifier in target["roles"]:
                    candidate = await membership_state(
                        uow.connection, target, {"id": identifier, **values}
                    )
                    if candidate.roles:
                        self.access.member_delegation(member, grants, candidate, known_pairs)
            # 没有当前成员的角色也可能绑定授权；角色扩权不能激活操作者无权授予的资源动作。
            for grant in grants:
                if (
                    grant.grantee_type == "role"
                    and grant.grantee_id == identifier
                    and requested & set(grant.allowed_actions)
                ):
                    self.access.delegation(
                        member,
                        grants,
                        list(grant.environments),
                        list(grant.data_scopes),
                        requested & set(grant.allowed_actions),
                        grant.resource_type,
                        grant.resource_id,
                        known_pairs,
                    )
            repository = Repository(metadata.tables["custom_roles"], context.scope)
            if old:
                assert body.revision is not None
                await repository.change(uow, identifier, body.revision, values)
            else:
                await repository.add(uow, identifier, values)
            await append_event(
                uow,
                event_id,
                context.actor_id or "",
                context.request_id,
                "role:edit",
                "custom_role",
                identifier,
                ["name", "allowed_actions", "state"],
            )
        return next(value for value in await self.list(session) if value["id"] == identifier)
