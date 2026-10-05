"""渠道角色动作上限实时参与授权，编辑同时核查所有受影响成员的潜在授权。"""

from collections.abc import Sequence
from typing import Any

from pydantic import Field
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncConnection

from creativity_service.core.auth.authentication import AdminSession
from creativity_service.core.context import AuthContext, ControlScope
from creativity_service.core.database import Repository, transaction
from creativity_service.core.locking import record_key
from creativity_service.core.primitives import Contract, Revision, ServiceError, new_id
from creativity_service.modules.iam.access import AccessService
from creativity_service.modules.iam.accounts import current_actor
from creativity_service.modules.iam.audit import append_event
from creativity_service.modules.iam.authorization import (
    effective_actions,
    platform_actions,
    require_platform,
)
from creativity_service.modules.iam.menu_catalog import PAGES
from creativity_service.modules.iam.menus import ancestors, compatible, menu_rows, menu_view
from creativity_service.modules.iam.operations_tables import metadata
from creativity_service.modules.iam.repositories import (
    TABLES,
    membership_state,
    policy_key,
    role_catalog,
    rows,
    save,
)
from creativity_service.modules.iam.roles import (
    ACTION_NAMES,
    PLATFORM_ACTIONS,
    PLATFORM_ONLY_ACTIONS,
    ROLE_ACTIONS,
)


class RoleSave(Contract):
    revision: Revision | None = None
    name: str = Field(min_length=1, max_length=128)
    allowed_actions: list[str] = Field(min_length=1, max_length=100)
    active: bool = True
    menu_ids: list[str] | None = Field(default=None, max_length=1000)


class CustomRoles:
    def __init__(self, access: AccessService) -> None:
        self.access, self.engine = access, access.repository.engine

    @staticmethod
    def view(
        code: str, value: dict[str, Any], count: int, allowed: frozenset[str]
    ) -> dict[str, Any]:
        return dict(
            id=code,
            name=value["name"],
            builtin=value.get("builtin", False),
            revision=value["revision"],
            active=value["state"] == "ACTIVE",
            state_label="启用" if value["state"] == "ACTIVE" else "停用",
            allowed_actions=value["allowed_actions"],
            action_names=[ACTION_NAMES[a] for a in value["allowed_actions"]],
            member_count=count,
            menu_ids=value.get("menu_ids"),
            editable=not value.get("builtin", False) and set(value["allowed_actions"]) <= allowed,
            updated_at=value.get("updated_at"),
        )

    @staticmethod
    async def member_count(connection: AsyncConnection, channel_id: str, identifier: str) -> int:
        table = TABLES["platform_accounts" if channel_id == "system" else "channel_memberships"]
        field = table.c.platform_roles if channel_id == "system" else table.c.roles
        return (
            await connection.scalar(
                select(func.count())
                .select_from(table)
                .where(
                    table.c.channel_id == channel_id,
                    table.c.status == "ACTIVE",
                    field.contains([identifier]),
                )
            )
            or 0
        )

    async def context(self, session: AdminSession) -> AuthContext:
        if not isinstance(session.context, AuthContext):
            raise ServiceError("FORBIDDEN", "请先进入渠道工作区", 403)
        return await self.access.context(
            session, session.context.scope.channel_id, "membership:manage"
        )

    async def list(self, session: AdminSession) -> list[dict[str, Any]]:
        channel_id, allowed = await self.scope_actions(session)
        async with self.engine.connect() as connection:
            catalog = await role_catalog(connection, channel_id)
            table = TABLES["platform_accounts" if channel_id == "system" else "channel_memberships"]
            field = table.c.platform_roles if channel_id == "system" else table.c.roles
            count_rows = (
                await connection.execute(
                    select(func.jsonb_array_elements_text(field).label("role_id"), func.count())
                    .where(table.c.channel_id == channel_id, table.c.status == "ACTIVE")
                    .group_by("role_id")
                )
            ).all()
            counts = {str(r[0]): int(r[1]) for r in count_rows}
        return [
            self.view(code, value, counts.get(code, 0), allowed) for code, value in catalog.items()
        ]

    async def scope_actions(self, session: AdminSession) -> tuple[str, frozenset[str]]:
        if not isinstance(session.context, AuthContext):
            await self.access.authentication.revalidate_admin(session)
            account = await self.access.authentication.active_account(session.account.id)
            require_platform(account, "role:grant")
            return "system", platform_actions(account)
        context = await self.context(session)
        policy = await self.access.authorization.read_policy(context)
        return context.scope.channel_id, policy.actions("channel", context.scope.channel_id)

    async def options(self, session: AdminSession) -> dict[str, Any]:
        channel_id, allowed = await self.scope_actions(session)
        scope = "platform" if channel_id == "system" else "channel"
        allowed = (
            allowed & PLATFORM_ACTIONS if scope == "platform" else allowed - PLATFORM_ONLY_ACTIONS
        )
        async with self.engine.connect() as connection:
            values = await menu_rows(connection)
        catalog = {r["id"]: r for r in values}
        items = []
        for row in values:
            if any(not compatible(n["workspace"], scope) for n in [*ancestors(row, catalog), row]):
                continue
            if row["kind"] == "BUTTON" and row["action_key"] not in allowed:
                continue
            if row["kind"] == "MENU" and not set(PAGES[row["page_key"]][2]) & allowed:
                continue
            items.append(menu_view(row))
        return {
            "scope": scope,
            "scope_name": "平台" if scope == "platform" else "渠道",
            "menus": items,
            "actions": [{"value": a, "label": ACTION_NAMES[a]} for a in sorted(allowed)],
        }

    @staticmethod
    async def validate_menus(
        connection: AsyncConnection, identifiers: Sequence[str] | None, scope: str
    ) -> None:
        if identifiers is None:
            return
        catalog = {r["id"]: r for r in await menu_rows(connection)}
        if len(set(identifiers)) != len(identifiers) or any(
            key not in catalog or not compatible(catalog[key]["workspace"], scope)
            for key in identifiers
        ):
            raise ServiceError("ROLE_MENU_INVALID", "菜单不存在、重复或不适用于当前工作区", 422)

    async def save(
        self, session: AdminSession, body: RoleSave, identifier: str | None = None
    ) -> dict[str, Any]:
        if not isinstance(session.context, AuthContext):
            return await self.save_platform(session, body, identifier)
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
            or requested & PLATFORM_ONLY_ACTIONS
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
            if old and not set(old["allowed_actions"]) <= allowed:
                raise ServiceError("GRANT_SCOPE_EXCEEDED", "无权修改超出本人授权范围的角色", 403)
            if not body.name.strip():
                raise ServiceError("VALIDATION_ERROR", "角色名称不能为空", 422)
            if any(
                r["name"] == body.name.strip() and code != identifier for code, r in catalog.items()
            ):
                raise ServiceError("ROLE_NAME_EXISTS", "角色名称已存在", 409)
            await self.validate_menus(uow.connection, body.menu_ids, "channel")
            values = {
                "name": body.name.strip(),
                "allowed_actions": sorted(requested),
                "state": "ACTIVE" if body.active else "DISABLED",
                "menu_ids": body.menu_ids,
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
                result = await repository.change(uow, identifier, body.revision, values)
            else:
                result = await repository.add(uow, identifier, values)
            await append_event(
                uow,
                event_id,
                context.actor_id or "",
                context.request_id,
                "role:edit",
                "custom_role",
                identifier,
                ["name", "allowed_actions", "menu_ids", "state"],
                target_name=body.name.strip(),
            )
            count = await self.member_count(uow.connection, context.scope.channel_id, identifier)
        # 写入已在锁内授权，响应不能因本次主动撤权变成失败，后续请求仍复核新权限。
        return self.view(identifier, result, count, allowed)

    async def save_platform(
        self, session: AdminSession, body: RoleSave, identifier: str | None
    ) -> dict[str, Any]:
        await self.scope_actions(session)
        creating = identifier is None
        identifier = identifier or new_id("role")
        event_id = new_id("audit")
        if identifier in ROLE_ACTIONS:
            raise ServiceError("BUILTIN_ROLE_READ_ONLY", "内置角色只读", 409)
        async with transaction(
            self.engine,
            ControlScope(purpose="roles", actor_id=session.account.id),
            [
                policy_key("system"),
                record_key("system", "custom_roles", identifier),
                record_key("system", "audit_events", event_id),
            ],
        ) as uow:
            actor = await current_actor(uow, session, "role:grant")
            catalog = await role_catalog(uow.connection, "system")
            old = catalog.get(identifier)
            if not creating and not old:
                raise ServiceError("NOT_FOUND", "平台角色不存在", 404)
            requested = frozenset(body.allowed_actions)
            if len(requested) != len(body.allowed_actions) or not requested <= PLATFORM_ACTIONS:
                raise ServiceError("ROLE_ACTIONS_INVALID", "平台角色只能选择平台操作权限", 422)
            if not requested <= platform_actions(actor) or (
                old and not set(old["allowed_actions"]) <= platform_actions(actor)
            ):
                raise ServiceError("GRANT_SCOPE_EXCEEDED", "角色权限不能超出当前可授权范围", 403)
            if not body.name.strip():
                raise ServiceError("VALIDATION_ERROR", "角色名称不能为空", 422)
            if any(
                r["name"] == body.name.strip() and code != identifier for code, r in catalog.items()
            ):
                raise ServiceError("ROLE_NAME_EXISTS", "角色名称已存在", 409)
            await self.validate_menus(uow.connection, body.menu_ids, "platform")
            result = await save(
                uow,
                "custom_roles",
                identifier,
                {
                    "name": body.name.strip(),
                    "allowed_actions": sorted(requested),
                    "state": "ACTIVE" if body.active else "DISABLED",
                    "menu_ids": body.menu_ids,
                },
                body.revision,
            )
            await append_event(
                uow,
                event_id,
                actor.id,
                session.context.request_id,
                "role:create" if creating else "role:edit",
                "custom_role",
                identifier,
                ["name", "allowed_actions", "menu_ids", "state"],
                target_name=body.name.strip(),
            )
            count = await self.member_count(uow.connection, "system", identifier)
        return self.view(identifier, result, count, platform_actions(actor))

    async def remove(self, session: AdminSession, identifier: str, revision: int) -> None:
        channel_id, _ = await self.scope_actions(session)
        if identifier in ROLE_ACTIONS:
            raise ServiceError("BUILTIN_ROLE_READ_ONLY", "内置角色不能删除", 409)
        event_id = new_id("audit")
        scope = (
            ControlScope(purpose="roles", actor_id=session.account.id)
            if channel_id == "system"
            else session.context.scope
        )
        async with transaction(
            self.engine,
            scope,
            [
                policy_key("system"),
                policy_key(channel_id),
                record_key(channel_id, "custom_roles", identifier),
                record_key(channel_id, "audit_events", event_id),
            ],
        ) as uow:
            if channel_id == "system":
                actor = await current_actor(uow, session, "role:grant")
                allowed = platform_actions(actor)
            else:
                member, grants = await self.access.locked_policy(uow, session, "membership:manage")
                assert isinstance(session.context, AuthContext)
                allowed = effective_actions(
                    member,
                    grants,
                    session.context.scope.environment,
                    session.context.scope.data_scope_id or "",
                    "channel",
                    channel_id,
                )
            role = (await role_catalog(uow.connection, channel_id)).get(identifier)
            if not role:
                raise ServiceError("NOT_FOUND", "角色不存在", 404)
            if role["revision"] != revision:
                raise ServiceError("REVISION_CONFLICT", "角色已变化，请刷新后重试", 409)
            if not set(role["allowed_actions"]) <= allowed:
                raise ServiceError("GRANT_SCOPE_EXCEEDED", "无权修改该角色", 403)
            table = TABLES["platform_accounts" if channel_id == "system" else "channel_memberships"]
            field = table.c.platform_roles if channel_id == "system" else table.c.roles
            bound = await uow.connection.scalar(
                select(table.c.id)
                .where(table.c.channel_id == channel_id, field.contains([identifier]))
                .limit(1)
            )
            grants_table = TABLES["resource_grants"]
            grant = await uow.connection.scalar(
                select(grants_table.c.id)
                .where(
                    grants_table.c.channel_id == channel_id,
                    grants_table.c.grantee_type == "role",
                    grants_table.c.grantee_id == identifier,
                )
                .limit(1)
            )
            if bound or grant:
                raise ServiceError(
                    "ROLE_REFERENCED", "角色仍关联账号、成员或资源授权，请先解除关联", 409
                )
            table = TABLES["custom_roles"]
            await uow.connection.execute(
                delete(table).where(table.c.channel_id == channel_id, table.c.id == identifier)
            )
            await append_event(
                uow,
                event_id,
                session.account.id,
                session.context.request_id,
                "role:delete",
                "custom_role",
                identifier,
                ["name"],
                target_name=role["name"],
            )
