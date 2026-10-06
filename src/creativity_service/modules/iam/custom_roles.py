"""渠道角色动作上限实时参与授权，编辑同时核查所有受影响成员的潜在授权。"""

from collections.abc import Sequence
from typing import Any, Literal

from pydantic import Field
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncConnection
from sqlalchemy.sql import CompoundSelect, Select

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
    membership_from_catalog,
    policy_key,
    role_catalog,
    rows,
    save,
)
from creativity_service.modules.iam.roles import (
    ACTION_NAMES,
    ORDINARY_CHANNEL_ACTIONS,
    PLATFORM_ACTIONS,
    PLATFORM_ONLY_ACTIONS,
)


class RoleSave(Contract):
    revision: Revision | None = None
    name: str = Field(min_length=1, max_length=128)
    allowed_actions: list[str] = Field(min_length=1, max_length=100)
    active: bool = True
    menu_ids: list[str] | None = Field(default=None, max_length=1000)
    grant_scope: Literal["platform", "channel"] | None = None


class CustomRoles:
    def __init__(self, access: AccessService) -> None:
        self.access, self.engine = access, access.repository.engine

    @staticmethod
    def view(
        code: str,
        value: dict[str, Any],
        count: int,
        allowed: frozenset[str],
        *,
        managed: bool = True,
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
            grant_scope=value["grant_scope"],
            grant_scope_name="平台" if value["grant_scope"] == "platform" else "渠道",
            editable=managed
            and not value.get("builtin", False)
            and set(value["allowed_actions"]) <= allowed,
            updated_at=value.get("updated_at"),
        )

    @staticmethod
    async def member_count(connection: AsyncConnection, channel_id: str, identifier: str) -> int:
        accounts, members = TABLES["platform_accounts"], TABLES["channel_memberships"]
        member_bindings = select(members.c.user_id.label("user_id")).where(
            members.c.status == "ACTIVE",
            members.c.roles.contains([identifier]),
            members.c.channel_id != "system"
            if channel_id == "system"
            else members.c.channel_id == channel_id,
        )
        selected: Select[Any] | CompoundSelect[Any] = member_bindings
        if channel_id == "system":
            selected = member_bindings.union(
                select(accounts.c.id.label("user_id")).where(
                    accounts.c.channel_id == "system",
                    accounts.c.status == "ACTIVE",
                    accounts.c.platform_roles.contains([identifier])
                    | accounts.c.role_ids.contains([identifier])
                    | (accounts.c.role_id == identifier),
                )
            )
        return await connection.scalar(select(func.count()).select_from(selected.subquery())) or 0

    async def context(self, session: AdminSession) -> AuthContext:
        if not isinstance(session.context, AuthContext):
            raise ServiceError("FORBIDDEN", "请先进入渠道工作区", 403)
        return await self.access.context(
            session, session.context.scope.channel_id, "membership:manage"
        )

    async def list(self, session: AdminSession) -> list[dict[str, Any]]:
        channel_id, allowed = await self.scope_actions(session)
        async with self.engine.connect() as connection:
            catalog = await role_catalog(connection, channel_id, all_scopes=channel_id == "system")
            if channel_id == "system":
                # 兼容角色留在渠道成员目录，平台账号角色管理只维护可分配的管理角色。
                catalog = {
                    code: role for code, role in catalog.items() if role["account_assignable"]
                }
            if channel_id == "system" and "channel:govern" not in allowed:
                catalog = {
                    code: role
                    for code, role in catalog.items()
                    if role["grant_scope"] == "platform"
                }
            table = TABLES["platform_accounts" if channel_id == "system" else "channel_memberships"]
            field = table.c.platform_roles if channel_id == "system" else table.c.roles
            account_bindings = select(
                func.jsonb_array_elements_text(field).label("role_id"),
                table.c.id.label("user_id"),
            ).where(table.c.channel_id == channel_id, table.c.status == "ACTIVE")
            bindings: Select[Any] | CompoundSelect[Any] = account_bindings
            if channel_id == "system":
                members = TABLES["channel_memberships"]
                bindings = account_bindings.union(
                    select(func.jsonb_array_elements_text(table.c.role_ids), table.c.id).where(
                        table.c.channel_id == "system",
                        table.c.status == "ACTIVE",
                        func.jsonb_typeof(table.c.role_ids) == "array",
                    ),
                    select(table.c.role_id, table.c.id).where(
                        table.c.channel_id == "system",
                        table.c.status == "ACTIVE",
                        table.c.role_id.is_not(None),
                    ),
                    select(
                        func.jsonb_array_elements_text(members.c.roles), members.c.user_id
                    ).where(members.c.channel_id != "system", members.c.status == "ACTIVE"),
                )
            linked = bindings.subquery()
            count_rows = (
                await connection.execute(
                    select(linked.c.role_id, func.count(func.distinct(linked.c.user_id)))
                    .where(linked.c.role_id.in_(catalog))
                    .group_by(linked.c.role_id)
                )
            ).all()
            counts = {str(r[0]): int(r[1]) for r in count_rows}
        return [
            self.view(
                code,
                value,
                counts.get(code, 0),
                self.manageable_actions(allowed, value["grant_scope"]),
                managed=value["channel_id"] == channel_id,
            )
            for code, value in catalog.items()
        ]

    @staticmethod
    def manageable_actions(allowed: frozenset[str], scope: str) -> frozenset[str]:
        if scope == "platform":
            return allowed & PLATFORM_ACTIONS
        if "channel:govern" in allowed:
            return ORDINARY_CHANNEL_ACTIONS
        return allowed - PLATFORM_ONLY_ACTIONS

    async def scope_actions(self, session: AdminSession) -> tuple[str, frozenset[str]]:
        if not isinstance(session.context, AuthContext):
            await self.access.authentication.revalidate_admin(session)
            account = await self.access.authentication.active_account(session.account.id)
            require_platform(account, "role:grant")
            return "system", platform_actions(account)
        context = await self.context(session)
        policy = await self.access.authorization.read_policy(context)
        return context.scope.channel_id, policy.actions("channel", context.scope.channel_id)

    async def options(
        self, session: AdminSession, grant_scope: str | None = None
    ) -> dict[str, Any]:
        channel_id, allowed = await self.scope_actions(session)
        scopes = ["platform"] if channel_id == "system" else ["channel"]
        if channel_id == "system" and "channel:govern" in allowed:
            scopes.append("channel")
        scope = grant_scope or scopes[0]
        if scope not in scopes:
            raise ServiceError("FORBIDDEN", "无权维护此作用域的角色", 403)
        allowed = self.manageable_actions(allowed, scope)
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
            "scopes": [
                {"value": s, "label": "平台" if s == "platform" else "渠道"} for s in scopes
            ],
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
        if body.grant_scope not in {None, "channel"}:
            raise ServiceError("ROLE_ACTIONS_INVALID", "渠道只能维护渠道角色", 422)
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
            if old and old["channel_id"] != context.scope.channel_id:
                raise ServiceError("ROLE_READ_ONLY", "共享角色须在平台角色管理中维护", 409)
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
                "grant_scope": "channel",
            }
            candidate_catalog = {**catalog, identifier: {"id": identifier, **values}}
            for target in await rows(
                uow.connection, "channel_memberships", context.scope.channel_id
            ):
                if identifier in target["roles"]:
                    candidate = membership_from_catalog(target, candidate_catalog)
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
        async with transaction(
            self.engine,
            ControlScope(purpose="roles", actor_id=session.account.id),
            [
                policy_key("system"),
                record_key("system", "custom_roles", identifier),
                record_key("system", "audit_events", event_id),
            ],
        ) as uow:
            catalog = await role_catalog(uow.connection, "system", all_scopes=True)
            actor = await current_actor(uow, session, "role:grant", catalog=catalog)
            old = catalog.get(identifier)
            if not creating and not old:
                raise ServiceError("NOT_FOUND", "平台角色不存在", 404)
            if old and old["builtin"]:
                raise ServiceError("BUILTIN_ROLE_READ_ONLY", "内置角色只读", 409)
            scope = body.grant_scope or (old["grant_scope"] if old else "platform")
            if old and old["grant_scope"] != scope:
                raise ServiceError(
                    "ROLE_SCOPE_IMMUTABLE", "已有角色不能变更作用域，请新建角色", 409
                )
            if scope == "channel":
                require_platform(actor, "channel:govern")
            allowed = self.manageable_actions(platform_actions(actor), scope)
            requested = frozenset(body.allowed_actions)
            ceiling = PLATFORM_ACTIONS if scope == "platform" else ORDINARY_CHANNEL_ACTIONS
            if len(requested) != len(body.allowed_actions) or not requested <= ceiling:
                raise ServiceError("ROLE_ACTIONS_INVALID", "角色动作重复或不适用于当前作用域", 422)
            if not requested <= allowed or (old and not set(old["allowed_actions"]) <= allowed):
                raise ServiceError("GRANT_SCOPE_EXCEEDED", "角色权限不能超出当前可授权范围", 403)
            if not body.name.strip():
                raise ServiceError("VALIDATION_ERROR", "角色名称不能为空", 422)
            if any(
                r["name"] == body.name.strip() and code != identifier for code, r in catalog.items()
            ):
                raise ServiceError("ROLE_NAME_EXISTS", "角色名称已存在", 409)
            await self.validate_menus(uow.connection, body.menu_ids, scope)
            result = await save(
                uow,
                "custom_roles",
                identifier,
                {
                    "name": body.name.strip(),
                    "allowed_actions": sorted(requested),
                    "state": "ACTIVE" if body.active else "DISABLED",
                    "menu_ids": body.menu_ids,
                    "grant_scope": scope,
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
        return self.view(identifier, result, count, allowed)

    async def remove(self, session: AdminSession, identifier: str, revision: int) -> None:
        channel_id, _ = await self.scope_actions(session)
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
            role = (
                await role_catalog(uow.connection, channel_id, all_scopes=channel_id == "system")
            ).get(identifier)
            if not role:
                raise ServiceError("NOT_FOUND", "角色不存在", 404)
            if role["builtin"]:
                raise ServiceError("BUILTIN_ROLE_READ_ONLY", "内置角色不能删除", 409)
            if role["channel_id"] != channel_id:
                raise ServiceError("ROLE_READ_ONLY", "共享角色须在平台角色管理中维护", 409)
            if channel_id == "system" and role["grant_scope"] == "channel":
                require_platform(actor, "channel:govern")
            allowed = self.manageable_actions(allowed, role["grant_scope"])
            if role["revision"] != revision:
                raise ServiceError("REVISION_CONFLICT", "角色已变化，请刷新后重试", 409)
            if not set(role["allowed_actions"]) <= allowed:
                raise ServiceError("GRANT_SCOPE_EXCEEDED", "无权修改该角色", 403)
            table = TABLES["platform_accounts" if channel_id == "system" else "channel_memberships"]
            field = table.c.platform_roles if channel_id == "system" else table.c.roles
            bound = await uow.connection.scalar(
                select(table.c.id)
                .where(
                    table.c.channel_id == channel_id,
                    field.contains([identifier])
                    | table.c.role_ids.contains([identifier])
                    | (table.c.role_id == identifier)
                    if channel_id == "system"
                    else field.contains([identifier]),
                )
                .limit(1)
            )
            grants_table = TABLES["resource_grants"]
            members_table = TABLES["channel_memberships"]
            shared_member = (
                await uow.connection.scalar(
                    select(members_table.c.id)
                    .where(
                        members_table.c.channel_id != "system",
                        members_table.c.roles.contains([identifier]),
                    )
                    .limit(1)
                )
                if channel_id == "system" and role["grant_scope"] == "channel"
                else None
            )
            grant = await uow.connection.scalar(
                select(grants_table.c.id)
                .where(
                    grants_table.c.channel_id != "system"
                    if channel_id == "system" and role["grant_scope"] == "channel"
                    else grants_table.c.channel_id == channel_id,
                    grants_table.c.grantee_type == "role",
                    grants_table.c.grantee_id == identifier,
                )
                .limit(1)
            )
            if bound or grant or shared_member:
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
