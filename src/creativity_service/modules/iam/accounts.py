"""账号生命周期与初始化；登录名及凭据变更在公共互斥事务内完成。"""

from typing import Any

from creativity_service.core.auth.authentication import AdminSession, AuthenticationService
from creativity_service.core.auth.passwords import PasswordHasher, normalize_login
from creativity_service.core.auth.types import AccountState
from creativity_service.core.context import ControlScope
from creativity_service.core.database import UnitOfWork, transaction
from creativity_service.core.locking import ResourceKey, record_key
from creativity_service.core.primitives import ServiceError, new_id, utcnow
from creativity_service.modules.iam.audit import append_event, audit_denials
from creativity_service.modules.iam.authorization import require_platform
from creativity_service.modules.iam.repositories import (
    IdentityRepository,
    one,
    policy_key,
    rows,
    save,
    to_state,
)
from creativity_service.modules.iam.revocations import RevocationService, enqueue
from creativity_service.modules.iam.roles import ROLE_ACTIONS, ROLE_NAMES
from creativity_service.modules.iam.schemas import (
    AccountCreate,
    AccountUpdate,
    AccountView,
    PasswordChange,
    PasswordReset,
)


def account_view(row: dict[str, Any]) -> AccountView:
    return AccountView(
        user_id=row["id"],
        login_name=row["login_name"],
        display_name=row["display_name"],
        platform_roles=row["platform_roles"],
        platform_role_names=[ROLE_NAMES[r] for r in row["platform_roles"]],
        status=row["status"],
        status_label="启用" if row["status"] == "ACTIVE" else "停用",
        must_change_password=row["must_change_password"],
        revision=row["revision"],
        credential_updated_at=row["credential_updated_at"],
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
    account = to_state(AccountState, row)
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

    async def list(self, session: AdminSession, limit: int = 100) -> list[AccountView]:
        await self.authentication.revalidate_admin(session)
        require_platform(
            await self.authentication.active_account(session.account.id), "account:manage"
        )
        if not 1 <= limit <= 200:
            raise ServiceError("VALIDATION_ERROR", "查询数量须在 1 至 200 之间", 422)
        async with self.repository.engine.connect() as connection:
            result = await rows(connection, "platform_accounts", "system")
        return [account_view(row) for row in sorted(result, key=lambda r: r["login_name"])[:limit]]

    @audit_denials("account:create", "account", None)
    async def create(self, session: AdminSession, body: AccountCreate) -> AccountView:
        await self.authentication.revalidate_admin(session)
        require_platform(
            await self.authentication.active_account(session.account.id), "account:manage"
        )
        name = normalize_login(body.login_name)
        password_hash = await self.passwords.hash(body.initial_password.get_secret_value())
        user_id, event_id = new_id("user"), new_id("audit")
        keys = [
            policy_key("system"),
            ResourceKey("system", "login-name", (name,)),
            record_key("system", "platform_accounts", user_id),
            record_key("system", "audit_events", event_id),
        ]
        async with transaction(
            self.repository.engine,
            ControlScope(purpose="accounts", actor_id=session.account.id),
            keys,
        ) as uow:
            actor = await current_actor(uow, session, "account:manage")
            if body.platform_roles:
                require_platform(actor, "role:grant")
                if not set(body.platform_roles) <= set(actor.platform_roles):
                    raise ServiceError("FORBIDDEN", "不能授予本人没有的平台角色", 403)
            if await one(uow.connection, "platform_accounts", "system", login_name=name):
                raise ServiceError("LOGIN_NAME_EXISTS", "登录名已存在", 409)
            row = await self._insert(uow, user_id, body, name, password_hash)
            await append_event(
                uow,
                event_id,
                actor.id,
                session.context.request_id,
                "account:create",
                "account",
                user_id,
            )
        return account_view(row)

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
        event_id, revoke_id = new_id("audit"), new_id("revoke")
        keys = [
            policy_key("system"),
            record_key("system", "platform_accounts", user_id),
            record_key("system", "audit_events", event_id),
            record_key("system", "iam_revocations", revoke_id),
        ]
        revocation = None
        async with transaction(
            self.repository.engine,
            ControlScope(purpose="accounts", actor_id=session.account.id),
            keys,
        ) as uow:
            actor = await current_actor(uow, session, "account:manage")
            row = await one(uow.connection, "platform_accounts", "system", id=user_id)
            if row is None:
                raise ServiceError("NOT_FOUND", "账号不存在", 404)
            values = body.model_dump(exclude={"revision"}, exclude_none=True)
            if "display_name" in values:
                values["display_name"] = values["display_name"].strip()
                if not values["display_name"]:
                    raise ServiceError("VALIDATION_ERROR", "显示名称不能为空", 422)
            if body.platform_roles is not None:
                require_platform(actor, "role:grant")
                if not set(body.platform_roles) <= set(actor.platform_roles):
                    raise ServiceError("FORBIDDEN", "不能授予本人没有的平台角色", 403)
            changed_fields = list(values)
            if any(
                key in values and values[key] != row[key] for key in ("status", "platform_roles")
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
        return account_view(row)

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
            await current_actor(uow, session, "account:manage" if initial else None)
            row = await one(uow.connection, "platform_accounts", "system", id=user_id)
            if row is None:
                raise ServiceError("NOT_FOUND", "账号不存在", 404)
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
