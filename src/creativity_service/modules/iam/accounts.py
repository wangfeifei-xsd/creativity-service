"""账号生命周期与初始化；登录名及凭据变更在公共互斥事务内完成。"""

from collections.abc import Sequence
from typing import Any

from sqlalchemy import func, or_, select

from creativity_service.core.auth.authentication import AdminSession, AuthenticationService
from creativity_service.core.auth.passwords import PasswordHasher, normalize_login
from creativity_service.core.auth.types import AccountState
from creativity_service.core.context import ControlScope, Scope
from creativity_service.core.contracts import VisibleAction
from creativity_service.core.database import UnitOfWork, control_transaction, transaction
from creativity_service.core.locking import ResourceKey, record_key
from creativity_service.core.primitives import ServiceError, new_id, utcnow
from creativity_service.modules.iam.account_channels import (
    account_channels,
    assignment_keys,
    assignment_scopes,
    synchronize_channels,
)
from creativity_service.modules.iam.audit import append_event, audit_denials
from creativity_service.modules.iam.authorization import platform_actions, require_platform
from creativity_service.modules.iam.repositories import (
    TABLES,
    IdentityRepository,
    account_state,
    one,
    policy_key,
    role_catalog,
    rows,
    save,
)
from creativity_service.modules.iam.revocations import RevocationService, enqueue
from creativity_service.modules.iam.roles import ACTION_NAMES, ROLE_ACTIONS, ROLE_NAMES
from creativity_service.modules.iam.schemas import (
    AccountCreate,
    AccountUpdate,
    AccountView,
    AdministratorRole,
    DirectoryPage,
    PasswordChange,
    PasswordReset,
    RoleView,
)


def account_view(
    row: dict[str, Any],
    catalog: dict[str, dict[str, Any]] | None = None,
    channels: list[dict[str, Any]] | None = None,
) -> AccountView:
    role: AdministratorRole = "platform_admin" if row["platform_roles"] else "channel_admin"
    return AccountView(
        user_id=row["id"],
        login_name=row["login_name"],
        display_name=row["display_name"],
        platform_roles=row["platform_roles"],
        platform_role_names=[
            (catalog or {}).get(r, {}).get("name", ROLE_NAMES.get(r, "角色不可用"))
            for r in row["platform_roles"]
        ],
        role=role,
        role_name=ROLE_NAMES[role],
        channel_ids=[c["channel_id"] for c in channels or []],
        channel_names=[c["channel_name"] for c in channels or []],
        status=row["status"],
        status_label="启用" if row["status"] == "ACTIVE" else "停用",
        must_change_password=row["must_change_password"],
        revision=row["revision"],
        credential_updated_at=row["credential_updated_at"],
        updated_at=row.get("updated_at"),
    )


async def current_actor(uow: UnitOfWork, session: AdminSession, action: str | None) -> AccountState:
    uow.require_lock(policy_key("system"))
    row = await one(uow.connection, "platform_accounts", "system", id=session.account.id)
    if (
        row is None
        or row["status"] != "ACTIVE"
        or row["credential_version"] != session.token.credential_version
    ):
        raise ServiceError("UNAUTHENTICATED", "当前身份已失效", 401)
    # 同一个策略锁覆盖退出、账号权限变更与授权写入，避免检查后并发扩权。
    if await rows(
        uow.connection,
        "iam_revocations",
        session.token.channel_id,
        kind="token",
        target_id=session.token.token_digest,
    ):
        raise ServiceError("UNAUTHENTICATED", "会话已退出", 401)
    if session.token.expires_at <= utcnow():
        raise ServiceError("UNAUTHENTICATED", "会话已到期", 401)
    if session.token.purpose == "management":
        member = await one(
            uow.connection,
            "channel_memberships",
            session.token.channel_id,
            user_id=session.account.id,
        )
        if (
            member is None
            or member["status"] != "ACTIVE"
            or member["revision"] != session.token.membership_version
        ):
            raise ServiceError("MEMBERSHIP_DISABLED", "渠道成员身份已变更", 401)
    account = await account_state(uow.connection, row)
    if action:
        require_platform(account, action)
    return account


class AccountService:
    def __init__(
        self,
        repository: IdentityRepository,
        authentication: AuthenticationService,
        passwords: PasswordHasher,
        revocations: RevocationService,
    ) -> None:
        self.repository, self.authentication = repository, authentication
        self.passwords, self.revocations = passwords, revocations

    async def role_options(self, session: AdminSession) -> list[RoleView]:
        await self.authentication.revalidate_admin(session)
        actor = await self.authentication.active_account(session.account.id)
        require_platform(actor, "account:manage")
        actions = platform_actions(actor)
        return [
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
            for code in ("platform_admin", "channel_admin")
            if "role:grant" in actions
            and (
                ROLE_ACTIONS[code] <= actions
                if code == "platform_admin"
                else "channel:govern" in actions
            )
        ]

    async def page(
        self,
        session: AdminSession,
        limit: int = 50,
        offset: int = 0,
        search: str = "",
        status: str | None = None,
    ) -> DirectoryPage[AccountView]:
        await self.authentication.revalidate_admin(session)
        require_platform(
            await self.authentication.active_account(session.account.id), "account:manage"
        )
        if not 1 <= limit <= 200 or offset < 0 or status not in {None, "ACTIVE", "DISABLED"}:
            raise ServiceError("VALIDATION_ERROR", "分页或状态条件不正确", 422)
        table = TABLES["platform_accounts"]
        predicates = [table.c.channel_id == "system"]
        if search.strip():
            predicates.append(
                or_(
                    table.c.login_name.icontains(search.strip(), autoescape=True),
                    table.c.display_name.icontains(search.strip(), autoescape=True),
                )
            )
        if status:
            predicates.append(table.c.status == status)
        async with self.repository.engine.connect() as connection:
            catalog = await role_catalog(connection, "system")
            total = await connection.scalar(
                select(func.count()).select_from(table).where(*predicates)
            )
            found = (
                await connection.execute(
                    select(table)
                    .where(*predicates)
                    .order_by(table.c.login_name, table.c.id)
                    .offset(offset)
                    .limit(limit)
                )
            ).mappings()
            page_rows = [dict(row) for row in found]
            channels = await account_channels(connection, [row["id"] for row in page_rows])
            items = [account_view(row, catalog, channels.get(row["id"], [])) for row in page_rows]
        return DirectoryPage(items=items, total=total or 0, offset=offset, limit=limit)

    async def list(
        self,
        session: AdminSession,
        limit: int = 100,
        offset: int = 0,
        search: str = "",
        status: str | None = None,
    ) -> list[AccountView]:
        return (await self.page(session, limit, offset, search, status)).items

    async def get(self, session: AdminSession, user_id: str) -> AccountView:
        await self.authentication.revalidate_admin(session)
        require_platform(
            await self.authentication.active_account(session.account.id), "account:manage"
        )
        async with self.repository.engine.connect() as connection:
            row = await one(connection, "platform_accounts", "system", id=user_id)
            if not row:
                raise ServiceError("NOT_FOUND", "账号不存在", 404)
            channels = await account_channels(connection, [user_id])
            return account_view(
                row, await role_catalog(connection, "system"), channels.get(user_id, [])
            )

    @staticmethod
    def administrator_values(
        body: AccountCreate | AccountUpdate,
    ) -> tuple[Sequence[str] | None, Sequence[str] | None]:
        """两种管理身份映射到既有平台角色与渠道成员，兼容已有细粒度授权接口。"""
        if body.role is None:
            if body.channel_ids is not None:
                raise ServiceError("VALIDATION_ERROR", "配置渠道时须选择管理员角色", 422)
            return None, None
        roles = ["platform_admin"] if body.role == "platform_admin" else []
        if body.platform_roles and body.platform_roles != roles:
            raise ServiceError("VALIDATION_ERROR", "管理员角色与平台授权不一致", 422)
        channels = body.channel_ids
        if body.role == "platform_admin":
            if channels:
                raise ServiceError("VALIDATION_ERROR", "请为渠道管理员配置授权渠道", 422)
            channels = []
        if channels is not None and (len(channels) != len(set(channels)) or "system" in channels):
            raise ServiceError("VALIDATION_ERROR", "授权渠道重复或无效", 422)
        return roles, channels

    @staticmethod
    async def validate_roles(uow: UnitOfWork, actor: AccountState, codes: Sequence[str]) -> None:
        require_platform(actor, "role:grant")
        catalog = await role_catalog(uow.connection, "system")
        if len(codes) != len(set(codes)) or any(
            c not in catalog or catalog[c]["state"] != "ACTIVE" for c in codes
        ):
            raise ServiceError("ROLE_UNAVAILABLE", "平台角色不存在、重复或已停用", 422)
        actions = {a for c in codes for a in catalog[c]["allowed_actions"]}
        if not actions <= platform_actions(actor):
            raise ServiceError("FORBIDDEN", "不能授予超出本人权限的平台角色", 403)

    @staticmethod
    async def validate_target(uow: UnitOfWork, actor: AccountState, row: dict[str, Any]) -> None:
        """账号维护和重置不能接管具有更高平台权限的身份。"""
        catalog = await role_catalog(uow.connection, "system")
        actions = {
            action
            for code in row["platform_roles"]
            for action in catalog.get(code, {}).get("allowed_actions", [])
        }
        if not actions <= platform_actions(actor):
            raise ServiceError("FORBIDDEN", "无权维护权限超出本人范围的账号", 403)

    @staticmethod
    async def protect_last_admin(
        uow: UnitOfWork, row: dict[str, Any], values: dict[str, Any]
    ) -> None:
        if row["status"] != "ACTIVE" or "platform_admin" not in row["platform_roles"]:
            return
        if values.get("status", row["status"]) == "ACTIVE" and "platform_admin" in values.get(
            "platform_roles", row["platform_roles"]
        ):
            return
        table = TABLES["platform_accounts"]
        other = await uow.connection.scalar(
            select(table.c.id)
            .where(
                table.c.channel_id == "system",
                table.c.id != row["id"],
                table.c.status == "ACTIVE",
                table.c.platform_roles.contains(["platform_admin"]),
            )
            .limit(1)
        )
        if not other:
            raise ServiceError("LAST_PLATFORM_ADMIN", "至少保留一个启用的平台管理员", 409)

    @audit_denials("account:create", "account", None)
    async def create(self, session: AdminSession, body: AccountCreate) -> AccountView:
        await self.authentication.revalidate_admin(session)
        require_platform(
            await self.authentication.active_account(session.account.id), "account:manage"
        )
        name = normalize_login(body.login_name)
        password_hash = await self.passwords.hash(body.initial_password.get_secret_value())
        user_id, event_id = new_id("user"), new_id("audit")
        roles, selected = self.administrator_values(body)
        if roles is not None:
            body = body.model_copy(update={"platform_roles": roles})
        selected = list(selected or [])
        async with self.repository.engine.connect() as connection:
            scopes = await assignment_scopes(connection, user_id, selected)
        keys = [
            policy_key("system"),
            ResourceKey("system", "login-name", (name,)),
            record_key("system", "platform_accounts", user_id),
            record_key("system", "audit_events", event_id),
            *(
                key
                for scope in scopes
                for key in assignment_keys(scope.channel_id, user_id, event_id)
            ),
        ]
        async with control_transaction(
            self.repository.engine,
            ControlScope(purpose="accounts", actor_id=session.account.id),
            scopes,
            keys,
        ) as units:
            uow = units["system"]
            actor = await current_actor(uow, session, "account:manage")
            if roles is not None:
                require_platform(actor, "role:grant")
            if body.platform_roles:
                await self.validate_roles(uow, actor, body.platform_roles)
            if await one(uow.connection, "platform_accounts", "system", login_name=name):
                raise ServiceError("LOGIN_NAME_EXISTS", "登录名已存在", 409)
            row = await self._insert(uow, user_id, body, name, password_hash)
            if selected:
                await synchronize_channels(
                    units, actor, user_id, selected, event_id, session.context.request_id
                )
            await append_event(
                uow,
                event_id,
                actor.id,
                session.context.request_id,
                "account:create",
                "account",
                user_id,
            )
        async with self.repository.engine.connect() as connection:
            channels = await account_channels(connection, [user_id])
            return account_view(
                row, await role_catalog(connection, "system"), channels.get(user_id, [])
            )

    async def _insert(
        self, uow: UnitOfWork, user_id: str, body: AccountCreate, name: str, password_hash: str
    ) -> dict[str, Any]:
        if not body.display_name.strip():
            raise ServiceError("VALIDATION_ERROR", "显示名称不能为空", 422)
        return await save(
            uow,
            "platform_accounts",
            user_id,
            {
                "login_name": name,
                "display_name": body.display_name.strip(),
                "password_hash": password_hash,
                "platform_roles": list(body.platform_roles),
                "status": "ACTIVE",
                "must_change_password": True,
                "credential_updated_at": utcnow(),
                "credential_version": 1,
            },
        )

    @audit_denials("account:update", "account", 0)
    async def update(self, session: AdminSession, user_id: str, body: AccountUpdate) -> AccountView:
        await self.authentication.revalidate_admin(session)
        roles, selected = self.administrator_values(body)
        selected = list(selected) if selected is not None else None
        event_id, revoke_id = new_id("audit"), new_id("revoke")
        async with self.repository.engine.connect() as connection:
            scopes: Sequence[Scope] = (
                await assignment_scopes(connection, user_id, selected)
                if selected is not None
                else []
            )
        keys = [
            policy_key("system"),
            record_key("system", "platform_accounts", user_id),
            record_key("system", "audit_events", event_id),
            record_key("system", "iam_revocations", revoke_id),
            *(
                key
                for scope in scopes
                for key in assignment_keys(scope.channel_id, user_id, event_id)
            ),
        ]
        revocation = None
        async with control_transaction(
            self.repository.engine,
            ControlScope(purpose="accounts", actor_id=session.account.id),
            list(scopes),
            keys,
        ) as units:
            uow = units["system"]
            actor = await current_actor(uow, session, "account:manage")
            row = await one(uow.connection, "platform_accounts", "system", id=user_id)
            if row is None:
                raise ServiceError("NOT_FOUND", "账号不存在", 404)
            await self.validate_target(uow, actor, row)
            values = body.model_dump(exclude={"revision", "role", "channel_ids"}, exclude_none=True)
            if roles is not None:
                values["platform_roles"] = roles
                require_platform(actor, "role:grant")
            if "display_name" in values:
                values["display_name"] = values["display_name"].strip()
                if not values["display_name"]:
                    raise ServiceError("VALIDATION_ERROR", "显示名称不能为空", 422)
            if "platform_roles" in values:
                await self.validate_roles(uow, actor, values["platform_roles"])
            await self.protect_last_admin(uow, row, values)
            changed_fields = list(values)
            channel_changed = False
            if selected is not None:
                channel_changed = await synchronize_channels(
                    units, actor, user_id, selected, event_id, session.context.request_id
                )
                if channel_changed:
                    changed_fields.append("channel_ids")
            if (
                any(
                    key in values and values[key] != row[key]
                    for key in ("status", "platform_roles")
                )
                or channel_changed
            ):
                values["credential_version"] = row["credential_version"] + 1
                revocation = await enqueue(uow, revoke_id, "account", user_id)
            row = await save(uow, "platform_accounts", user_id, values, body.revision)
            await append_event(
                uow,
                event_id,
                actor.id,
                session.context.request_id,
                "account:update",
                "account",
                user_id,
                changed_fields,
            )
        if revocation:
            await self.revocations.complete(revocation)
        async with self.repository.engine.connect() as connection:
            channels = await account_channels(connection, [user_id])
            return account_view(
                row, await role_catalog(connection, "system"), channels.get(user_id, [])
            )

    @audit_denials("account:reset", "account", 0)
    async def reset(self, session: AdminSession, user_id: str, body: PasswordReset) -> None:
        await self.authentication.revalidate_admin(session)
        require_platform(
            await self.authentication.active_account(session.account.id), "account:manage"
        )
        hashed = await self.passwords.hash(body.initial_password.get_secret_value())
        await self._replace_password(session, user_id, hashed, body.revision, initial=True)

    @audit_denials("auth:password", "account", None)
    async def change_password(self, session: AdminSession, body: PasswordChange) -> None:
        await self.authentication.revalidate_admin(session, allow_initial=True)
        account = await self.repository.credentials(session.account.login_name)
        if account is None or not await self.passwords.verify(
            body.current_password.get_secret_value(), account["password_hash"]
        ):
            raise ServiceError("PASSWORD_INCORRECT", "原密码不正确", 403)
        if body.current_password.get_secret_value() == body.new_password.get_secret_value():
            raise ServiceError("PASSWORD_UNCHANGED", "新密码不能与原密码相同", 422)
        hashed = await self.passwords.hash(body.new_password.get_secret_value())
        await self._replace_password(
            session, session.account.id, hashed, account["revision"], initial=False
        )

    async def _replace_password(
        self, session: AdminSession, user_id: str, hashed: str, revision: int, *, initial: bool
    ) -> None:
        event_id, revoke_id = new_id("audit"), new_id("revoke")
        keys = [
            policy_key("system"),
            record_key("system", "platform_accounts", user_id),
            record_key("system", "audit_events", event_id),
            record_key("system", "iam_revocations", revoke_id),
        ]
        async with transaction(
            self.repository.engine,
            ControlScope(purpose="accounts", actor_id=session.account.id),
            keys,
        ) as uow:
            actor = await current_actor(uow, session, "account:manage" if initial else None)
            row = await one(uow.connection, "platform_accounts", "system", id=user_id)
            if row is None:
                raise ServiceError("NOT_FOUND", "账号不存在", 404)
            if initial:
                await self.validate_target(uow, actor, row)
            await save(
                uow,
                "platform_accounts",
                user_id,
                {
                    "password_hash": hashed,
                    "credential_version": row["credential_version"] + 1,
                    "credential_updated_at": utcnow(),
                    "must_change_password": initial,
                },
                revision,
            )
            item = await enqueue(uow, revoke_id, "account", user_id)
            await append_event(
                uow,
                event_id,
                session.account.id,
                session.context.request_id,
                "account:reset" if initial else "auth:password",
                "account",
                user_id,
                ["password"],
            )
        await self.revocations.complete(item)

    async def initialize_admin(self, body: AccountCreate) -> AccountView:
        name = normalize_login(body.login_name)
        password_hash = await self.passwords.hash(body.initial_password.get_secret_value())
        user_id, event_id = new_id("user"), new_id("audit")
        keys = [
            policy_key("system"),
            ResourceKey("system", "login-name", (name,)),
            record_key("system", "platform_accounts", user_id),
            record_key("system", "audit_events", event_id),
            *(record_key("system", "builtin_roles", f"role_{code}") for code in ROLE_NAMES),
        ]
        async with transaction(
            self.repository.engine, ControlScope(purpose="accounts", actor_id="deployment"), keys
        ) as uow:
            if await rows(uow.connection, "platform_accounts", "system"):
                raise ServiceError(
                    "ADMIN_ALREADY_INITIALIZED", "管理员已初始化，请使用账号管理接口", 409
                )
            for code, label in ROLE_NAMES.items():
                await save(
                    uow,
                    "builtin_roles",
                    f"role_{code}",
                    {
                        "role_code": code,
                        "name": label,
                        "allowed_actions": sorted(ROLE_ACTIONS[code]),
                        "grant_scope": "platform" if code == "platform_admin" else "channel",
                    },
                )
            body = body.model_copy(update={"platform_roles": ["platform_admin"]})
            row = await self._insert(uow, user_id, body, name, password_hash)
            await append_event(
                uow,
                event_id,
                "deployment",
                new_id("request"),
                "account:initialize",
                "account",
                user_id,
            )
        return account_view(row)
