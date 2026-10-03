"""成员和资源授权的唯一写服务，页面入口与渠道模块共同复用。"""

import re
from typing import Any

from creativity_service.core.auth.authentication import AdminSession, AuthenticationService
from creativity_service.core.auth.types import GrantState, MembershipState, WorkspaceDirectory
from creativity_service.core.context import AuthContext, Scope, require_channel_state
from creativity_service.core.contracts import VisibleAction
from creativity_service.core.database import UnitOfWork, transaction
from creativity_service.core.locking import ResourceKey, record_key
from creativity_service.core.primitives import ServiceError, digest, new_id, unavailable
from creativity_service.modules.iam.accounts import current_actor
from creativity_service.modules.iam.audit import append_event, audit_denials, audit_ranges
from creativity_service.modules.iam.authorization import (
    IamAuthorization,
    action_allowed,
    effective_actions,
    require_platform,
)
from creativity_service.modules.iam.repositories import (
    IdentityRepository,
    membership_state,
    one,
    policy_key,
    resolved_actions,
    role_catalog,
    rows,
    save,
    to_state,
)
from creativity_service.modules.iam.revocations import RevocationService, enqueue
from creativity_service.modules.iam.roles import (
    ACTION_NAMES,
    INDEPENDENT_ACTIONS,
    ROLE_ACTIONS,
    ROLE_NAMES,
    role_actions,
)
from creativity_service.modules.iam.schemas import (
    GrantInput,
    GrantView,
    MembershipInput,
    MembershipView,
    RoleView,
)

ENVIRONMENT_NAMES = {"dev": "开发", "test": "测试", "fat": "验收", "prod": "生产"}


def membership_id(channel_id: str, user_id: str) -> str:
    return "member_" + digest([channel_id, user_id])[:40]


class AccessService:
    def __init__(
        self,
        repository: IdentityRepository,
        authentication: AuthenticationService,
        authorization: IamAuthorization,
        revocations: RevocationService,
        directory: WorkspaceDirectory | None = None,
    ) -> None:
        self.repository, self.authentication, self.authorization = (
            repository,
            authentication,
            authorization,
        )
        self.revocations, self.directory = revocations, directory

    async def context(self, session: AdminSession, channel_id: str, action: str) -> AuthContext:
        if not isinstance(session.context, AuthContext):
            raise ServiceError("FORBIDDEN", "请先进入获授权的渠道工作区", 403)
        context = session.context
        if channel_id != context.scope.channel_id:
            raise ServiceError("NOT_FOUND", "请求资源不存在", 404)
        await self.authorization.boundary(context, action, "channel", channel_id)
        return context

    async def locked_policy(
        self, uow: UnitOfWork, session: AdminSession, action: str
    ) -> tuple[MembershipState, list[GrantState]]:
        await current_actor(uow, session, None)
        if session.account.must_change_password or not isinstance(session.context, AuthContext):
            raise ServiceError("FORBIDDEN", "无权执行此操作", 403)
        uow.require_lock(policy_key(uow.scope.channel_id))
        row = await one(
            uow.connection, "channel_memberships", uow.scope.channel_id, user_id=session.account.id
        )
        if (
            row is None
            or row["status"] != "ACTIVE"
            or row["revision"] != session.token.membership_version
        ):
            raise ServiceError("MEMBERSHIP_DISABLED", "成员身份已变更", 401)
        member = await membership_state(uow.connection, row)
        grants = [
            to_state(GrantState, row)
            for row in await rows(uow.connection, "resource_grants", uow.scope.channel_id)
        ]
        scope = session.context.scope
        actions = effective_actions(
            member,
            grants,
            scope.environment,
            scope.data_scope_id or "",
            "channel",
            scope.channel_id,
        )
        if not action_allowed(actions, action):
            raise ServiceError("FORBIDDEN", "无权执行此操作", 403)
        return member, grants

    async def workspace_pairs(self, context: AuthContext) -> set[tuple[str, str]]:
        if self.directory is None:
            raise unavailable("渠道工作区目录")
        return {
            (o.environment, o.data_scope_id)
            for o in await self.directory.list_for(context.actor_id or "")
            if o.channel_id == context.scope.channel_id
        }

    def delegation(
        self,
        member: MembershipState,
        grants: list[GrantState],
        environments: list[str],
        data_scopes: list[str],
        actions: frozenset[str],
        resource_type: str,
        resource_id: str,
        known_pairs: set[tuple[str, str]],
    ) -> None:
        pairs = {(e, d) for e, d in known_pairs if e in environments and d in data_scopes}
        if (
            not actions
            or not environments
            or not data_scopes
            or {e for e, _ in pairs} != set(environments)
            or {d for _, d in pairs} != set(data_scopes)
        ):
            raise ServiceError("GRANT_SCOPE_EXCEEDED", "授权范围不在可管理的工作区内", 403)
        for environment, data_scope in pairs:
            effective = effective_actions(
                member, grants, environment, data_scope, resource_type, resource_id
            )
            if not actions <= effective:
                raise ServiceError("GRANT_SCOPE_EXCEEDED", "不能超出本人的可授权范围", 403)

    async def validate_scopes(
        self,
        context: AuthContext,
        environments: list[str],
        data_scopes: list[str],
        known_pairs: set[tuple[str, str]],
    ) -> None:
        if len(environments) != len(set(environments)) or len(data_scopes) != len(set(data_scopes)):
            raise ServiceError("VALIDATION_ERROR", "授权范围不能重复", 422)
        pairs = {(e, d) for e, d in known_pairs if e in environments and d in data_scopes}
        if {e for e, _ in pairs} != set(environments) or {d for _, d in pairs} != set(data_scopes):
            raise ServiceError("NOT_FOUND", "请求的工作区不存在", 404)
        # 数据域只属于一个环境，不能将两个数组的笛卡尔积当成真实业务范围。
        for environment, data_scope in pairs:
            scope = Scope.model_validate(
                {
                    "channel_id": context.scope.channel_id,
                    "environment": environment,
                    "data_scope_id": data_scope,
                }
            )
            await require_channel_state(
                context.model_copy(update={"scope": scope}), self.authentication.channels
            )

    def member_delegation(
        self,
        actor: MembershipState,
        grants: list[GrantState],
        target: MembershipState,
        known_pairs: set[tuple[str, str]],
    ) -> None:
        self.delegation(
            actor,
            grants,
            list(target.environments),
            list(target.data_scopes),
            role_actions(target.roles) | target.custom_actions,
            "channel",
            target.channel_id,
            known_pairs,
        )
        # 调整角色或数据域会激活已有账号/角色授权，必须连同这些潜在权限检查。
        active_target = target.model_copy(update={"status": "ACTIVE"})
        for grant in grants:
            for environment in target.environments:
                for data_scope in target.data_scopes:
                    if (environment, data_scope) not in known_pairs:
                        continue
                    actions = effective_actions(
                        active_target,
                        grants,
                        environment,
                        data_scope,
                        grant.resource_type,
                        grant.resource_id,
                    )
                    if actions:
                        self.delegation(
                            actor,
                            grants,
                            [environment],
                            [data_scope],
                            actions,
                            grant.resource_type,
                            grant.resource_id,
                            known_pairs,
                        )

    @audit_denials("membership:put", "account", 1)
    async def put_member(
        self, session: AdminSession, channel_id: str, user_id: str, body: MembershipInput
    ) -> MembershipView:
        context = await self.context(session, channel_id, "membership:manage")
        known_pairs = await self.workspace_pairs(context)
        await self.validate_scopes(
            context, list(body.environments), list(body.data_scopes), known_pairs
        )
        if len(set(body.roles)) != len(body.roles):
            raise ServiceError("VALIDATION_ERROR", "角色不能重复", 422)
        member_id, event_id, revoke_id = (
            membership_id(channel_id, user_id),
            new_id("audit"),
            new_id("revoke"),
        )
        keys = self.member_keys(channel_id, user_id) + [
            record_key(channel_id, "audit_events", event_id),
            record_key(channel_id, "iam_revocations", revoke_id),
        ]
        async with transaction(self.repository.engine, context.scope, keys) as uow:
            member, grants = await self.locked_policy(uow, session, "membership:manage")
            await self.validate_scopes(
                context, list(body.environments), list(body.data_scopes), known_pairs
            )
            self.delegation(
                member,
                grants,
                list(body.environments),
                list(body.data_scopes),
                await resolved_actions(
                    uow.connection, channel_id, list(body.roles), require_active=True
                ),
                "channel",
                channel_id,
                known_pairs,
            )
            target = await one(uow.connection, "platform_accounts", "system", id=user_id)
            if target is None or target["status"] != "ACTIVE":
                raise ServiceError("NOT_FOUND", "可用账号不存在", 404)
            existing = await one(uow.connection, "channel_memberships", channel_id, user_id=user_id)
            if existing and existing["id"] != member_id:
                raise ServiceError("STORAGE_INVARIANT_BROKEN", "成员记录标识不一致", 503)
            if existing:
                self.member_delegation(
                    member, grants, await membership_state(uow.connection, existing), known_pairs
                )
            candidate = MembershipState(
                id=member_id,
                channel_id=channel_id,
                user_id=user_id,
                roles=list(body.roles),
                environments=body.environments,
                data_scopes=body.data_scopes,
                status=body.status,
                revision=1,
            )
            candidate = await membership_state(uow.connection, candidate.model_dump(mode="json"))
            self.member_delegation(member, grants, candidate, known_pairs)
            row = await save(
                uow,
                "channel_memberships",
                member_id,
                {
                    "user_id": user_id,
                    "roles": list(body.roles),
                    "environments": list(body.environments),
                    "data_scopes": list(body.data_scopes),
                    "status": body.status,
                    "granted_by": session.account.id,
                },
                body.revision,
            )
            item = await enqueue(uow, revoke_id, "member", user_id)
            await append_event(
                uow,
                event_id,
                session.account.id,
                context.request_id,
                "membership:put",
                "account",
                user_id,
                ["roles", "environments", "data_scopes", "status"],
                affected_scopes=audit_ranges(
                    (list(body.environments), list(body.data_scopes)),
                    (existing["environments"], existing["data_scopes"]) if existing else ([], []),
                ),
            )
        await self.revocations.complete(item)
        return await self.member_view(row, session.account.id)

    def member_keys(self, channel_id: str, user_id: str) -> list[ResourceKey]:
        return [
            policy_key("system"),
            policy_key(channel_id),
            ResourceKey(channel_id, "membership", (user_id,)),
            record_key(channel_id, "channel_memberships", membership_id(channel_id, user_id)),
        ]

    @audit_denials("membership:remove", "account", 1)
    async def remove_member(
        self, session: AdminSession, channel_id: str, user_id: str, revision: int
    ) -> None:
        context = await self.context(session, channel_id, "membership:manage")
        known_pairs = await self.workspace_pairs(context)
        event_id, revoke_id = new_id("audit"), new_id("revoke")
        async with transaction(
            self.repository.engine,
            context.scope,
            self.member_keys(channel_id, user_id)
            + [
                record_key(channel_id, "audit_events", event_id),
                record_key(channel_id, "iam_revocations", revoke_id),
            ],
        ) as uow:
            actor, grants = await self.locked_policy(uow, session, "membership:manage")
            row = await one(uow.connection, "channel_memberships", channel_id, user_id=user_id)
            if row is None:
                raise ServiceError("NOT_FOUND", "成员不存在", 404)
            self.delegation(
                actor,
                grants,
                row["environments"],
                row["data_scopes"],
                await resolved_actions(uow.connection, channel_id, row["roles"]),
                "channel",
                channel_id,
                known_pairs,
            )
            await save(uow, "channel_memberships", row["id"], {"status": "DISABLED"}, revision)
            item = await enqueue(uow, revoke_id, "member", user_id)
            await append_event(
                uow,
                event_id,
                session.account.id,
                context.request_id,
                "membership:remove",
                "account",
                user_id,
                ["status"],
                affected_scopes=audit_ranges((row["environments"], row["data_scopes"])),
            )
        await self.revocations.complete(item)

    async def list_members(self, session: AdminSession, channel_id: str) -> list[MembershipView]:
        await self.context(session, channel_id, "membership:read")
        actor = (
            await self.authentication.active_member(session.context)
            if isinstance(session.context, AuthContext)
            else None
        )
        async with self.repository.engine.connect() as connection:
            result = await rows(connection, "channel_memberships", channel_id)
        # 只暴露操作者完整可见的成员范围，避免通过授权页面获知其他数据域。
        return [
            await self.member_view(row, session.account.id)
            for row in result
            if actor
            and set(row["environments"]) <= set(actor.environments)
            and set(row["data_scopes"]) <= set(actor.data_scopes)
        ]

    async def member_view(self, row: dict[str, Any], viewer_id: str) -> MembershipView:
        account = await self.repository.account(row["user_id"])
        options = await self.directory.list_for(viewer_id) if self.directory else []
        names = {
            option.data_scope_id: option.data_scope_name
            for option in options
            if option.channel_id == row["channel_id"]
        }
        async with self.repository.engine.connect() as connection:
            catalog = await role_catalog(connection, row["channel_id"])
        return MembershipView(
            user_id=row["user_id"],
            display_name=account.display_name if account else None,
            roles=row["roles"],
            role_names=[catalog[r]["name"] for r in row["roles"] if r in catalog],
            environments=row["environments"],
            environment_names=[ENVIRONMENT_NAMES[e] for e in row["environments"]],
            data_scopes=row["data_scopes"],
            data_scope_names=[names.get(s) for s in row["data_scopes"]],
            status=row["status"],
            status_label="启用" if row["status"] == "ACTIVE" else "停用",
            revision=row["revision"],
        )

    @audit_denials("grant:put", "resource_grant", 1)
    async def put_grant(
        self, session: AdminSession, channel_id: str, grant_id: str, body: GrantInput
    ) -> GrantView:
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,63}", grant_id):
            raise ServiceError("VALIDATION_ERROR", "授权标识不正确", 422)
        context = await self.context(session, channel_id, "grant:manage")
        if set(body.allowed_actions) - ACTION_NAMES.keys() or len(body.allowed_actions) != len(
            set(body.allowed_actions)
        ):
            raise ServiceError("VALIDATION_ERROR", "授权动作无效或重复", 422)
        known_pairs = await self.workspace_pairs(context)
        await self.validate_scopes(
            context, list(body.environments), list(body.data_scopes), known_pairs
        )
        for environment in body.environments:
            for data_scope in body.data_scopes:
                if (environment, data_scope) not in known_pairs:
                    continue
                candidate = context.model_copy(
                    update={
                        "scope": Scope(
                            channel_id=channel_id, environment=environment, data_scope_id=data_scope
                        )
                    }
                )
                await self.authorization.verify_resource(
                    candidate, body.resource_type, body.resource_id
                )
        event_id = new_id("audit")
        async with transaction(
            self.repository.engine,
            context.scope,
            [
                policy_key("system"),
                policy_key(channel_id),
                record_key(channel_id, "resource_grants", grant_id),
                record_key(channel_id, "audit_events", event_id),
                ResourceKey(
                    channel_id,
                    "resource-grant",
                    (body.grantee_type, body.grantee_id, body.resource_type, body.resource_id),
                ),
            ],
        ) as uow:
            actor, grants = await self.locked_policy(uow, session, "grant:manage")
            await self.validate_scopes(
                context, list(body.environments), list(body.data_scopes), known_pairs
            )
            self.delegation(
                actor,
                grants,
                list(body.environments),
                list(body.data_scopes),
                frozenset(body.allowed_actions),
                body.resource_type,
                body.resource_id,
                known_pairs,
            )
            if body.grantee_type == "role":
                ceiling = (
                    await resolved_actions(
                        uow.connection, channel_id, [body.grantee_id], require_active=True
                    )
                    | INDEPENDENT_ACTIONS
                )
            else:
                member = await one(
                    uow.connection, "channel_memberships", channel_id, user_id=body.grantee_id
                )
                if member is None or member["status"] != "ACTIVE":
                    raise ServiceError("NOT_FOUND", "有效渠道成员不存在", 404)
                if not set(body.environments) <= set(member["environments"]) or not set(
                    body.data_scopes
                ) <= set(member["data_scopes"]):
                    raise ServiceError("GRANT_SCOPE_EXCEEDED", "授权不能超出成员范围", 403)
                ceiling = (
                    await resolved_actions(
                        uow.connection, channel_id, member["roles"], require_active=True
                    )
                    | INDEPENDENT_ACTIONS
                )
            if not set(body.allowed_actions) <= ceiling:
                raise ServiceError("GRANT_SCOPE_EXCEEDED", "授权动作超出目标角色范围", 403)
            existing = await one(uow.connection, "resource_grants", channel_id, id=grant_id)
            if existing and existing["allowed_actions"]:
                self.delegation(
                    actor,
                    grants,
                    existing["environments"],
                    existing["data_scopes"],
                    frozenset(existing["allowed_actions"]),
                    existing["resource_type"],
                    existing["resource_id"],
                    known_pairs,
                )
            if existing and any(
                existing[k] != getattr(body, k)
                for k in ("grantee_type", "grantee_id", "resource_type", "resource_id")
            ):
                raise ServiceError("IMMUTABLE_FIELD", "授权对象与资源不能改绑", 422)
            duplicate = await one(
                uow.connection,
                "resource_grants",
                channel_id,
                grantee_type=body.grantee_type,
                grantee_id=body.grantee_id,
                resource_type=body.resource_type,
                resource_id=body.resource_id,
            )
            if duplicate and duplicate["id"] != grant_id:
                raise ServiceError("GRANT_EXISTS", "该资源授权已存在", 409)
            row = await save(
                uow,
                "resource_grants",
                grant_id,
                body.model_dump(exclude={"revision"}),
                body.revision,
            )
            await append_event(
                uow,
                event_id,
                session.account.id,
                context.request_id,
                "grant:put",
                "resource_grant",
                grant_id,
                ["allowed_actions", "environments", "data_scopes"],
                affected_scopes=audit_ranges(
                    (list(body.environments), list(body.data_scopes)),
                    (existing["environments"], existing["data_scopes"]) if existing else ([], []),
                ),
            )
        return await self.grant_view(row, context)

    @audit_denials("grant:revoke", "resource_grant", 1)
    async def revoke_grant(
        self, session: AdminSession, channel_id: str, grant_id: str, revision: int
    ) -> None:
        context = await self.context(session, channel_id, "grant:manage")
        known_pairs = await self.workspace_pairs(context)
        event_id = new_id("audit")
        async with transaction(
            self.repository.engine,
            context.scope,
            [
                policy_key("system"),
                policy_key(channel_id),
                record_key(channel_id, "resource_grants", grant_id),
                record_key(channel_id, "audit_events", event_id),
            ],
        ) as uow:
            member, grants = await self.locked_policy(uow, session, "grant:manage")
            row = await one(uow.connection, "resource_grants", channel_id, id=grant_id)
            if row is None:
                raise ServiceError("NOT_FOUND", "授权记录不存在", 404)
            if row["allowed_actions"]:
                self.delegation(
                    member,
                    grants,
                    row["environments"],
                    row["data_scopes"],
                    frozenset(row["allowed_actions"]),
                    row["resource_type"],
                    row["resource_id"],
                    known_pairs,
                )
            await save(uow, "resource_grants", grant_id, {"allowed_actions": []}, revision)
            await append_event(
                uow,
                event_id,
                session.account.id,
                context.request_id,
                "grant:revoke",
                "resource_grant",
                grant_id,
                ["allowed_actions"],
                affected_scopes=audit_ranges((row["environments"], row["data_scopes"])),
            )

    async def list_grants(self, session: AdminSession, channel_id: str) -> list[GrantView]:
        context = await self.context(session, channel_id, "grant:read")
        member = await self.authentication.active_member(context)
        async with self.repository.engine.connect() as connection:
            result = await rows(connection, "resource_grants", channel_id)
        return [
            await self.grant_view(row, context)
            for row in result
            if row["allowed_actions"]
            and set(row["environments"]) <= set(member.environments)
            and set(row["data_scopes"]) <= set(member.data_scopes)
        ]

    async def grant_view(self, row: dict[str, Any], context: AuthContext) -> GrantView:
        options = await self.directory.list_for(context.principal_id) if self.directory else []
        scope_names = {
            o.data_scope_id: o.data_scope_name
            for o in options
            if o.channel_id == context.scope.channel_id
        }
        account = (
            await self.repository.account(row["grantee_id"])
            if row["grantee_type"] == "account"
            else None
        )
        resource_name = None
        if row["resource_type"] == "channel" and self.directory:
            resource_name = next(
                (o.channel_name for o in options if o.channel_id == row["resource_id"]), None
            )
        elif row["resource_id"] == "*":
            resource_name = "该类全部资源"
        elif self.authorization.resources:
            resource = await self.authorization.resources.read_current(
                context, row["resource_type"], row["resource_id"]
            )
            if resource and resource.scope.channel_id == context.scope.channel_id:
                resource_name = resource.name
        return GrantView(
            **{key: row[key] for key in GrantInput.model_fields},
            grant_id=row["id"],
            grantee_name=account.display_name
            if account
            else await self.role_name(row["channel_id"], row["grantee_id"]),
            resource_name=resource_name,
            action_names=[ACTION_NAMES[a] for a in row["allowed_actions"]],
            environment_names=[ENVIRONMENT_NAMES[e] for e in row["environments"]],
            data_scope_names=[scope_names.get(value) for value in row["data_scopes"]],
        )

    async def role_name(self, channel_id: str, code: str) -> str | None:
        async with self.repository.engine.connect() as connection:
            catalog = await role_catalog(connection, channel_id)
        return catalog[code]["name"] if code in catalog else None

    async def roles(self, session: AdminSession) -> list[RoleView]:
        await self.authentication.revalidate_admin(session)
        if not isinstance(session.context, AuthContext):
            require_platform(
                await self.authentication.active_account(session.account.id), "role:grant"
            )
            codes = ["platform_admin"]
        else:
            context = session.context
            member = await self.authentication.active_member(context)
            grants = await self.repository.grants(context.scope.channel_id)
            actions = effective_actions(
                member,
                grants,
                context.scope.environment,
                context.scope.data_scope_id or "",
                "channel",
                context.scope.channel_id,
            )
            codes = [
                code
                for code, ceiling in ROLE_ACTIONS.items()
                if code != "platform_admin"
                and "membership:manage" in actions
                and ceiling <= actions
            ]
        result = [
            RoleView(
                role_code=code,
                name=ROLE_NAMES[code],
                grant_scope="platform" if code == "platform_admin" else "channel",
                grant_scope_name="平台" if code == "platform_admin" else "渠道",
                actions=[
                    VisibleAction(action_key=a, label=ACTION_NAMES[a])
                    for a in sorted(ROLE_ACTIONS[code])
                ],
            )
            for code in codes
        ]
        if isinstance(session.context, AuthContext) and "membership:manage" in actions:
            async with self.repository.engine.connect() as connection:
                catalog = await role_catalog(connection, session.context.scope.channel_id)
            result.extend(
                RoleView(
                    role_code=code,
                    name=value["name"],
                    grant_scope="channel",
                    grant_scope_name="渠道",
                    actions=[
                        VisibleAction(action_key=a, label=ACTION_NAMES[a])
                        for a in value["allowed_actions"]
                    ],
                )
                for code, value in catalog.items()
                if not value["builtin"]
                and value["state"] == "ACTIVE"
                and set(value["allowed_actions"]) <= actions
            )
        return result

    def provisioning_keys(self, channel_id: str, user_id: str) -> list[ResourceKey]:
        return self.member_keys(channel_id, user_id) + [
            record_key(
                channel_id, "resource_grants", "initial_" + membership_id(channel_id, user_id)
            ),
            record_key(channel_id, "audit_events", "audit_" + membership_id(channel_id, user_id)),
        ]

    async def provision_first_member(
        self,
        uow: UnitOfWork,
        session: AdminSession,
        user_id: str,
        environments: list[str],
        data_scopes: list[str],
        independent_actions: list[str] | None = None,
    ) -> None:
        """05 在已核准渠道开通事务中调用；权限和渠道主档要一起回滚。"""
        actor = await current_actor(uow, session, "channel:create")
        require_platform(actor, "channel:govern")
        channel_id = uow.scope.channel_id
        if channel_id == "system" or not environments or not data_scopes:
            raise ServiceError("VALIDATION_ERROR", "首位成员必须归有效业务范围", 422)
        independent = set(independent_actions or [])
        if not independent <= INDEPENDENT_ACTIONS:
            raise ServiceError("VALIDATION_ERROR", "初始独立授权动作不正确", 422)
        uow.require_lock(policy_key(channel_id))
        if await rows(uow.connection, "channel_memberships", channel_id):
            raise ServiceError("MEMBER_ALREADY_INITIALIZED", "渠道成员已初始化", 409)
        account = await one(uow.connection, "platform_accounts", "system", id=user_id)
        if not account or account["status"] != "ACTIVE":
            raise ServiceError("NOT_FOUND", "可用账号不存在", 404)
        body = MembershipInput(
            roles=["channel_admin"], environments=environments, data_scopes=data_scopes
        )
        await save(
            uow,
            "channel_memberships",
            membership_id(channel_id, user_id),
            {
                **body.model_dump(exclude={"revision"}),
                "user_id": user_id,
                "granted_by": actor.id,
            },
        )
        await save(
            uow,
            "resource_grants",
            "initial_" + membership_id(channel_id, user_id),
            {
                "grantee_type": "account",
                "grantee_id": user_id,
                "resource_type": "channel",
                "resource_id": channel_id,
                "environments": environments,
                "data_scopes": data_scopes,
                "allowed_actions": sorted(ROLE_ACTIONS["channel_admin"] | independent),
            },
        )
        await append_event(
            uow,
            "audit_" + membership_id(channel_id, user_id),
            actor.id,
            session.context.request_id,
            "membership:put",
            "account",
            user_id,
            ["roles", "environments", "data_scopes", "allowed_actions"],
            affected_scopes=audit_ranges((environments, data_scopes)),
        )

    def workspace_provisioning_keys(
        self, channel_id: str, user_id: str, domain_id: str
    ) -> list[ResourceKey]:
        grant_id = "workspace_" + digest([user_id, domain_id])[:40]
        return self.member_keys(channel_id, user_id) + [
            record_key(channel_id, "resource_grants", grant_id),
            record_key(channel_id, "audit_events", grant_id),
        ]

    async def provision_workspace(
        self, uow: UnitOfWork, session: AdminSession, user_id: str, environment: str, domain_id: str
    ) -> None:
        """渠道服务创建新数据域时，平台显式选择管理员并在同一事务授予普通权限。"""
        await current_actor(uow, session, "channel:govern")
        if isinstance(session.context, AuthContext) or uow.scope.channel_id == "system":
            raise ServiceError("FORBIDDEN", "工作区初始授权须经平台治理入口", 403)
        channel_id = uow.scope.channel_id
        account = await one(uow.connection, "platform_accounts", "system", id=user_id)
        if not account or account["status"] != "ACTIVE":
            raise ServiceError("NOT_FOUND", "可用账号不存在", 404)
        existing = await one(uow.connection, "channel_memberships", channel_id, user_id=user_id)
        if existing and existing["status"] != "ACTIVE":
            raise ServiceError("MEMBERSHIP_DISABLED", "请先恢复目标渠道成员", 409)
        await save(
            uow,
            "channel_memberships",
            membership_id(channel_id, user_id),
            {
                "user_id": user_id,
                "roles": sorted(set(existing["roles"] if existing else []) | {"channel_admin"}),
                "environments": sorted(
                    set(existing["environments"] if existing else []) | {environment}
                ),
                "data_scopes": sorted(
                    set(existing["data_scopes"] if existing else []) | {domain_id}
                ),
                "status": "ACTIVE",
                "granted_by": session.account.id,
            },
            existing["revision"] if existing else None,
        )
        grant_id = "workspace_" + digest([user_id, domain_id])[:40]
        await save(
            uow,
            "resource_grants",
            grant_id,
            {
                "grantee_type": "account",
                "grantee_id": user_id,
                "resource_type": "data_scope",
                "resource_id": domain_id,
                "environments": [environment],
                "data_scopes": [domain_id],
                "allowed_actions": sorted(ROLE_ACTIONS["channel_admin"]),
            },
        )
        await append_event(
            uow,
            grant_id,
            session.account.id,
            session.context.request_id,
            "membership:put",
            "account",
            user_id,
            ["roles", "environments", "data_scopes", "allowed_actions"],
            affected_scopes=audit_ranges(([environment], [domain_id])),
        )
