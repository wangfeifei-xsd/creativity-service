"""成员和资源授权的唯一写服务，页面入口与渠道模块共同复用。"""

import re
from typing import Any

from sqlalchemy import and_, select
from sqlalchemy.ext.asyncio import AsyncConnection

from creativity_service.core.auth.authentication import AdminSession, AuthenticationService
from creativity_service.core.auth.types import (
    GrantState,
    MembershipState,
    WorkspaceDirectory,
    WorkspaceOption,
)
from creativity_service.core.context import AuthContext, Scope, require_channel_state
from creativity_service.core.contracts import VisibleAction
from creativity_service.core.database import UnitOfWork, transaction
from creativity_service.core.locking import ResourceKey, record_key
from creativity_service.core.primitives import ServiceError, new_id, unavailable
from creativity_service.modules.iam.accounts import current_actor
from creativity_service.modules.iam.audit import append_event, audit_denials, audit_ranges
from creativity_service.modules.iam.authorization import (
    IamAuthorization,
    action_allowed,
    effective_actions,
    platform_actions,
    require_platform,
)
from creativity_service.modules.iam.display import resource_names
from creativity_service.modules.iam.reading import require_action
from creativity_service.modules.iam.repositories import (
    TABLES,
    IdentityRepository,
    membership_from_catalog,
    membership_id,
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
    PLATFORM_ASSIGNED_CHANNEL_ROLES,
    PLATFORM_ASSIGNED_ROLE_MESSAGE,
    require_channel_assignable_roles,
)
from creativity_service.modules.iam.schemas import (
    AccessAction,
    GrantInput,
    GrantView,
    MembershipInput,
    MembershipView,
    RoleView,
)

ENVIRONMENT_NAMES = {"dev": "开发", "test": "测试", "fat": "验收", "prod": "生产"}


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

    def channel_context(self, session: AdminSession, channel_id: str) -> AuthContext:
        if not isinstance(session.context, AuthContext):
            raise ServiceError("FORBIDDEN", "请先进入获授权的渠道环境", 403)
        context = session.context
        if channel_id != context.scope.channel_id:
            raise ServiceError("NOT_FOUND", "请求资源不存在", 404)
        return context

    async def context(self, session: AdminSession, channel_id: str, action: str) -> AuthContext:
        context = self.channel_context(session, channel_id)
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
            "channel",
            scope.channel_id,
        )
        if not action_allowed(actions, action):
            raise ServiceError("FORBIDDEN", "无权执行此操作", 403)
        return member, grants

    async def workspace_environments(self, context: AuthContext) -> set[str]:
        if self.directory is None:
            raise unavailable("渠道环境目录")
        return {
            option.environment
            for option in await self.directory.list_for(context.actor_id or "")
            if option.channel_id == context.scope.channel_id
        }

    def delegation(
        self,
        member: MembershipState,
        grants: list[GrantState],
        environments: list[str],
        actions: frozenset[str],
        resource_type: str,
        resource_id: str,
        known_environments: set[str],
    ) -> None:
        if not actions or not environments or not set(environments) <= known_environments:
            raise ServiceError("GRANT_SCOPE_EXCEEDED", "授权环境不在可管理范围内", 403)
        for environment in environments:
            if not actions <= effective_actions(
                member, grants, environment, resource_type, resource_id
            ):
                raise ServiceError("GRANT_SCOPE_EXCEEDED", "不能超出本人的可授权范围", 403)

    async def validate_scopes(
        self, context: AuthContext, environments: list[str], known_environments: set[str]
    ) -> None:
        if len(environments) != len(set(environments)):
            raise ServiceError("VALIDATION_ERROR", "授权环境不能重复", 422)
        if not set(environments) <= known_environments:
            raise ServiceError("NOT_FOUND", "请求的环境不存在", 404)
        for environment in environments:
            scope = Scope.model_validate(
                {"channel_id": context.scope.channel_id, "environment": environment}
            )
            await require_channel_state(
                context.model_copy(update={"scope": scope}), self.authentication.channels
            )

    def member_delegation(
        self,
        actor: MembershipState,
        grants: list[GrantState],
        target: MembershipState,
        known_environments: set[str],
    ) -> None:
        self.delegation(
            actor,
            grants,
            list(target.environments),
            target.custom_actions,
            "channel",
            target.channel_id,
            known_environments,
        )
        # 调整角色会激活已有授权，必须一起检查这些潜在权限。
        active_target = target.model_copy(update={"status": "ACTIVE"})
        for grant in grants:
            for environment in set(target.environments) & known_environments:
                actions = effective_actions(
                    active_target, grants, environment, grant.resource_type, grant.resource_id
                )
                if actions:
                    self.delegation(
                        actor,
                        grants,
                        [environment],
                        actions,
                        grant.resource_type,
                        grant.resource_id,
                        known_environments,
                    )

    @audit_denials("membership:put", "account", 1)
    async def put_member(
        self, session: AdminSession, channel_id: str, user_id: str, body: MembershipInput
    ) -> MembershipView:
        context = await self.context(session, channel_id, "membership:manage")
        known_environments = await self.workspace_environments(context)
        await self.validate_scopes(context, list(body.environments), known_environments)
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
            require_channel_assignable_roles(list(body.roles))
            requested_actions = await resolved_actions(
                uow.connection, channel_id, list(body.roles), require_active=True
            )
            await self.validate_scopes(context, list(body.environments), known_environments)
            self.delegation(
                member,
                grants,
                list(body.environments),
                requested_actions,
                "channel",
                channel_id,
                known_environments,
            )
            target = await one(uow.connection, "platform_accounts", "system", id=user_id)
            if target is None or target["status"] != "ACTIVE":
                raise ServiceError("NOT_FOUND", "可用账号不存在", 404)
            existing = await one(uow.connection, "channel_memberships", channel_id, user_id=user_id)
            if existing and existing["id"] != member_id:
                raise ServiceError("STORAGE_INVARIANT_BROKEN", "成员记录标识不一致", 503)
            if existing:
                require_channel_assignable_roles(existing["roles"])
                self.member_delegation(
                    member,
                    grants,
                    await membership_state(uow.connection, existing),
                    known_environments,
                )
            candidate = MembershipState(
                id=member_id,
                channel_id=channel_id,
                user_id=user_id,
                roles=list(body.roles),
                environments=body.environments,
                status=body.status,
                revision=1,
            )
            candidate = await membership_state(uow.connection, candidate.model_dump(mode="json"))
            self.member_delegation(member, grants, candidate, known_environments)
            row = await save(
                uow,
                "channel_memberships",
                member_id,
                {
                    "user_id": user_id,
                    "roles": list(body.roles),
                    "environments": list(body.environments),
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
                ["roles", "environments", "status"],
                affected_scopes=audit_ranges(
                    list(body.environments),
                    (existing["environments"] if existing else []),
                ),
            )
        await self.revocations.complete(item)
        # 写后提示使用事务中已验证的成员与授权；自改角色时按变更后的角色计算。
        saved = candidate.model_copy(update={"revision": row["revision"]})
        actor = saved if user_id == member.user_id else member
        return await self.member_view(
            row,
            session.account.id,
            actions=self.member_actions(
                saved,
                actor,
                grants,
                known_environments,
                effective_actions(
                    actor,
                    grants,
                    context.scope.environment,
                    "channel",
                    channel_id,
                ),
                account_active=True,
            ),
        )

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
        known_environments = await self.workspace_environments(context)
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
            require_channel_assignable_roles(row["roles"])
            self.delegation(
                actor,
                grants,
                row["environments"],
                await resolved_actions(uow.connection, channel_id, row["roles"]),
                "channel",
                channel_id,
                known_environments,
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
                affected_scopes=audit_ranges(row["environments"]),
            )
        await self.revocations.complete(item)

    async def list_members(self, session: AdminSession, channel_id: str) -> list[MembershipView]:
        context = self.channel_context(session, channel_id)
        policy = await self.authorization.read_policy(context)
        allowed = policy.actions("channel", channel_id)
        require_action(allowed, "membership:read")
        actor = policy.member
        if actor is None:
            raise ServiceError("FORBIDDEN", "请先进入获授权的渠道环境", 403)
        grants = list(policy.grants)
        async with self.repository.engine.connect() as connection:
            loaded = await rows(connection, "channel_memberships", channel_id)
        # 只暴露操作者完整可见的成员范围，避免通过授权页面获知其他环境。
        result = [row for row in loaded if set(row["environments"]) <= set(actor.environments)]
        options = await self.directory.for_member(actor) if self.directory else []
        known_environments: set[str] = {option.environment for option in options}
        data = await self.view_data(
            session.account.id, channel_id, [r["user_id"] for r in result], options=options
        )
        # 名称和角色目录已批量读取，逐行只构造成员状态并执行纯权限计算。
        return [
            await self.member_view(
                row,
                session.account.id,
                data,
                actions=self.member_actions(
                    membership_from_catalog(row, data["catalog"]),
                    actor,
                    grants,
                    known_environments,
                    allowed,
                    account_active=bool(
                        (account := data["accounts"].get(row["user_id"]))
                        and account.status == "ACTIVE"
                    ),
                ),
            )
            for row in result
        ]

    def member_actions(
        self,
        target: MembershipState,
        actor: MembershipState,
        grants: list[GrantState],
        known_environments: set[str],
        allowed: frozenset[str],
        *,
        account_active: bool,
    ) -> list[AccessAction]:
        """编辑需检查潜在授权，移除只检查角色范围；分别沿用写入原子校验。"""
        result = []
        for key, label in (("member:edit", "编辑"), ("member:remove", "移除成员")):
            reason = None
            if not action_allowed(allowed, "membership:manage"):
                reason = "没有渠道成员管理权限"
            elif PLATFORM_ASSIGNED_CHANNEL_ROLES.intersection(target.roles):
                reason = PLATFORM_ASSIGNED_ROLE_MESSAGE
            elif key == "member:edit" and not account_active:
                reason = "账号已停用或不存在"
            else:
                try:
                    if key == "member:edit":
                        self.member_delegation(actor, grants, target, known_environments)
                    else:
                        self.delegation(
                            actor,
                            grants,
                            list(target.environments),
                            target.custom_actions,
                            "channel",
                            target.channel_id,
                            known_environments,
                        )
                except ServiceError as exc:
                    if exc.code != "GRANT_SCOPE_EXCEEDED":
                        raise
                    reason = exc.message
            result.append(
                AccessAction(
                    action_key=key, label=label, enabled=reason is None, disabled_reason=reason
                )
            )
        return result

    async def view_data(
        self,
        viewer_id: str,
        channel_id: str,
        user_ids: list[str],
        *,
        options: list[WorkspaceOption] | None = None,
    ) -> dict[str, Any]:
        if options is None:
            options = await self.directory.list_for(viewer_id) if self.directory else []
        accounts = await self.repository.accounts(user_ids)
        async with self.repository.engine.connect() as connection:
            catalog = await role_catalog(connection, channel_id)
        return {"options": options, "accounts": accounts, "catalog": catalog}

    async def member_view(
        self,
        row: dict[str, Any],
        viewer_id: str,
        data: dict[str, Any] | None = None,
        *,
        actions: list[AccessAction],
    ) -> MembershipView:
        data = (
            data
            if data is not None
            else await self.view_data(viewer_id, row["channel_id"], [row["user_id"]])
        )
        account = data["accounts"].get(row["user_id"])
        catalog = data["catalog"]
        return MembershipView(
            user_id=row["user_id"],
            actions=actions,
            display_name=account.display_name if account else None,
            roles=row["roles"],
            role_names=[catalog[r]["name"] for r in row["roles"] if r in catalog],
            environments=row["environments"],
            environment_names=[ENVIRONMENT_NAMES[e] for e in row["environments"]],
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
        known_environments = await self.workspace_environments(context)
        await self.validate_scopes(context, list(body.environments), known_environments)
        for environment in body.environments:
            candidate = context.model_copy(
                update={"scope": Scope(channel_id=channel_id, environment=environment)}
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
            await self.validate_scopes(context, list(body.environments), known_environments)
            self.delegation(
                actor,
                grants,
                list(body.environments),
                frozenset(body.allowed_actions),
                body.resource_type,
                body.resource_id,
                known_environments,
            )
            if body.grantee_type == "role":
                require_channel_assignable_roles([body.grantee_id])
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
                if not set(body.environments) <= set(member["environments"]):
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
                    frozenset(existing["allowed_actions"]),
                    existing["resource_type"],
                    existing["resource_id"],
                    known_environments,
                )
            if existing and any(
                existing[k] != getattr(body, k)
                for k in ("grantee_type", "grantee_id", "resource_type", "resource_id")
            ):
                raise ServiceError("IMMUTABLE_FIELD", "授权对象与资源不能改绑", 422)
            duplicates = await rows(
                uow.connection,
                "resource_grants",
                channel_id,
                grantee_type=body.grantee_type,
                grantee_id=body.grantee_id,
                resource_type=body.resource_type,
                resource_id=body.resource_id,
            )
            if any(
                duplicate["id"] != grant_id
                and set(duplicate["environments"]) & set(body.environments)
                for duplicate in duplicates
            ):
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
                ["allowed_actions", "environments"],
                affected_scopes=audit_ranges(
                    list(body.environments),
                    (existing["environments"] if existing else []),
                ),
            )
        # 写后响应沿用事务中已校验的数据，并替换本次变更，避免自降权限后仍显示可操作。
        saved = to_state(GrantState, row)
        current_grants = [grant for grant in grants if grant.id != saved.id] + [saved]
        return await self.grant_view(
            row,
            context,
            actions=self.grant_actions(saved, context, actor, current_grants, known_environments),
        )

    @audit_denials("grant:revoke", "resource_grant", 1)
    async def revoke_grant(
        self, session: AdminSession, channel_id: str, grant_id: str, revision: int
    ) -> None:
        context = await self.context(session, channel_id, "grant:manage")
        known_environments = await self.workspace_environments(context)
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
            if row["grantee_type"] == "role":
                require_channel_assignable_roles([row["grantee_id"]])
            if row["allowed_actions"]:
                self.delegation(
                    member,
                    grants,
                    row["environments"],
                    frozenset(row["allowed_actions"]),
                    row["resource_type"],
                    row["resource_id"],
                    known_environments,
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
                affected_scopes=audit_ranges(row["environments"]),
            )

    async def list_grants(self, session: AdminSession, channel_id: str) -> list[GrantView]:
        context = self.channel_context(session, channel_id)
        policy = await self.authorization.read_policy(context)
        require_action(policy.actions("channel", channel_id), "grant:read")
        member = policy.member
        if member is None:
            raise ServiceError("FORBIDDEN", "请先进入获授权的渠道环境", 403)
        # 授权检查已读取完整的本渠道策略，列表与逐行操作直接复用，不能重新查成员和授权。
        grants = list(policy.grants)
        result = [
            grant
            for grant in grants
            if grant.allowed_actions and set(grant.environments) <= set(member.environments)
        ]
        options = await self.directory.for_member(member) if self.directory else []
        known_environments: set[str] = {option.environment for option in options}
        data = await self.view_data(
            context.principal_id,
            channel_id,
            [grant.grantee_id for grant in result if grant.grantee_type == "account"],
            options=options,
        )
        async with self.repository.engine.connect() as connection:
            data["resources"] = await resource_names(
                connection,
                self.authorization.resources,
                context,
                [
                    (grant.resource_type, grant.resource_id)
                    for grant in result
                    if grant.resource_type != "channel" and grant.resource_id != "*"
                ],
            )
        return [
            await self.grant_view(
                grant.model_dump(),
                context,
                data,
                actions=self.grant_actions(grant, context, member, grants, known_environments),
            )
            for grant in result
        ]

    def grant_actions(
        self,
        grant: GrantState,
        context: AuthContext,
        member: MembershipState,
        grants: list[GrantState],
        known_environments: set[str],
    ) -> list[AccessAction]:
        """行操作复用写入的委托范围校验；只计算权限提示，不替代写入事务复核。"""
        allowed = effective_actions(
            member,
            grants,
            context.scope.environment,
            "channel",
            context.scope.channel_id,
        )
        reason = None
        if not action_allowed(allowed, "grant:manage"):
            reason = "没有资源授权管理权限"
        elif grant.grantee_type == "role" and grant.grantee_id in PLATFORM_ASSIGNED_CHANNEL_ROLES:
            reason = PLATFORM_ASSIGNED_ROLE_MESSAGE
        else:
            try:
                self.delegation(
                    member,
                    grants,
                    list(grant.environments),
                    frozenset(grant.allowed_actions),
                    grant.resource_type,
                    grant.resource_id,
                    known_environments,
                )
            except ServiceError as exc:
                if exc.code != "GRANT_SCOPE_EXCEEDED":
                    raise
                reason = exc.message
        return [
            AccessAction(
                action_key=key, label=label, enabled=reason is None, disabled_reason=reason
            )
            for key, label in (("grant:edit", "编辑"), ("grant:revoke", "撤销授权"))
        ]

    async def grant_view(
        self,
        row: dict[str, Any],
        context: AuthContext,
        data: dict[str, Any] | None = None,
        *,
        actions: list[AccessAction],
    ) -> GrantView:
        data = (
            data
            if data is not None
            else await self.view_data(
                context.principal_id,
                row["channel_id"],
                [row["grantee_id"]] if row["grantee_type"] == "account" else [],
            )
        )
        options = data["options"]
        account = data["accounts"].get(row["grantee_id"])
        resource_name = None
        if row["resource_type"] == "channel" and self.directory:
            resource_name = next(
                (o.channel_name for o in options if o.channel_id == row["resource_id"]), None
            )
        elif row["resource_id"] == "*":
            resource_name = "该类全部资源"
        elif "resources" in data:
            resource_name = data["resources"].get((row["resource_type"], row["resource_id"]))
        elif self.authorization.resources:
            async with self.repository.engine.connect() as connection:
                names = await resource_names(
                    connection,
                    self.authorization.resources,
                    context,
                    [(row["resource_type"], row["resource_id"])],
                )
            resource_name = names.get((row["resource_type"], row["resource_id"]))
        return GrantView(
            **{key: row[key] for key in GrantInput.model_fields},
            grant_id=row["id"],
            actions=actions,
            grantee_name=account.display_name
            if account
            else data["catalog"].get(row["grantee_id"], {}).get("name"),
            resource_name=resource_name,
            action_names=[ACTION_NAMES[a] for a in row["allowed_actions"]],
            environment_names=[ENVIRONMENT_NAMES[e] for e in row["environments"]],
        )

    async def role_name(self, channel_id: str, code: str) -> str | None:
        async with self.repository.engine.connect() as connection:
            catalog = await role_catalog(connection, channel_id)
        return catalog[code]["name"] if code in catalog else None

    async def roles(self, session: AdminSession) -> list[RoleView]:
        await self.authentication.revalidate_admin(session)
        if not isinstance(session.context, AuthContext):
            account = await self.authentication.active_account(session.account.id)
            require_platform(account, "role:grant")
            actions = platform_actions(account)
            channel_id, scope, label = "system", "platform", "平台"
        else:
            context = session.context
            member = await self.authentication.active_member(context)
            grants = await self.repository.grants(context.scope.channel_id)
            actions = effective_actions(
                member,
                grants,
                context.scope.environment,
                "channel",
                context.scope.channel_id,
            )
            if "membership:manage" not in actions:
                return []
            channel_id, scope, label = context.scope.channel_id, "channel", "渠道"
        async with self.repository.engine.connect() as connection:
            catalog = await role_catalog(connection, channel_id)
        return [
            RoleView(
                role_code=code,
                name=value["name"],
                grant_scope=scope,
                grant_scope_name=label,
                actions=[
                    VisibleAction(action_key=a, label=ACTION_NAMES[a])
                    for a in value["allowed_actions"]
                ],
            )
            for code, value in catalog.items()
            if value["state"] == "ACTIVE"
            and set(value["allowed_actions"]) <= actions
            and (scope != "channel" or code not in PLATFORM_ASSIGNED_CHANNEL_ROLES)
        ]

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
        independent_actions: list[str] | None = None,
    ) -> None:
        """05 在已核准渠道开通事务中调用；权限和渠道主档要一起回滚。"""
        actor = await current_actor(uow, session, "channel:create")
        require_platform(actor, "channel:govern")
        channel_id = uow.scope.channel_id
        if channel_id == "system":
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
        # 分步开通只登记管理员身份；空范围不产生工作区访问权，也不代表全部环境。
        await save(
            uow,
            "channel_memberships",
            membership_id(channel_id, user_id),
            {
                "roles": ["channel_admin"],
                "environments": environments,
                "status": "ACTIVE",
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
                "allowed_actions": sorted(
                    await resolved_actions(
                        uow.connection, channel_id, ["channel_admin"], require_active=True
                    )
                    | independent
                ),
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
            ["roles", "environments", "allowed_actions"],
            affected_scopes=audit_ranges(environments) or None,
        )

    async def first_administrator(
        self, connection: AsyncConnection, channel_id: str
    ) -> dict[str, Any] | None:
        """按开通时的初始授权定位首位管理员，不能据此签发管理身份。"""
        members, grants, accounts = (
            TABLES[name] for name in ("channel_memberships", "resource_grants", "platform_accounts")
        )
        source = members.join(
            grants,
            and_(
                grants.c.channel_id == members.c.channel_id,
                grants.c.id == "initial_" + members.c.id,
                grants.c.grantee_type == "account",
                grants.c.grantee_id == members.c.user_id,
                grants.c.resource_type == "channel",
                grants.c.resource_id == channel_id,
            ),
        ).outerjoin(
            accounts,
            and_(accounts.c.channel_id == "system", accounts.c.id == members.c.user_id),
        )
        found = (
            (
                await connection.execute(
                    select(
                        members.c.user_id,
                        members.c.environments,
                        grants.c.environments.label("initial_environments"),
                        accounts.c.display_name,
                    )
                    .select_from(source)
                    .where(
                        members.c.channel_id == channel_id,
                        members.c.status == "ACTIVE",
                        members.c.roles.contains(["channel_admin"]),
                    )
                    .limit(2)
                )
            )
            .mappings()
            .all()
        )
        if len(found) > 1:
            raise ServiceError("STORAGE_INVARIANT_BROKEN", "首位管理员记录重复", 503)
        return dict(found[0]) if found else None
