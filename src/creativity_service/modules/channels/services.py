"""渠道开通与配置服务；检查、授权、写入和审计共用互斥事务。"""

import unicodedata
from typing import Any

from sqlalchemy.ext.asyncio import AsyncConnection

from creativity_service.core.auth.authentication import AdminSession
from creativity_service.core.auth.types import GrantState
from creativity_service.core.context import AuthContext, ControlScope, Scope
from creativity_service.core.contracts import VisibleAction
from creativity_service.core.database import UnitOfWork, transaction
from creativity_service.core.deletion import RecoveryService
from creativity_service.core.locking import ResourceKey, record_key
from creativity_service.core.primitives import Contract, ServiceError, new_id, unavailable, utcnow
from creativity_service.modules.channels.codes import channel_code
from creativity_service.modules.channels.initialization import system_channel_values
from creativity_service.modules.channels.ports import ResourceReferenceReader, UsageReader
from creativity_service.modules.channels.reading import ChannelReadData
from creativity_service.modules.channels.repositories import (
    ChannelRepository,
    environment_id,
    mapping_key,
    one,
    required,
    rows,
    save,
)
from creativity_service.modules.channels.schemas import (
    ChannelCreate,
    ChannelUpdate,
    ChannelView,
    ClientCreate,
    ClientUpdate,
    ClientView,
    DataScopeCreate,
    DataScopeUpdate,
    DataScopeView,
    EnvironmentCreate,
    EnvironmentUpdate,
    EnvironmentView,
    OverviewView,
    UsageQuery,
    UsageView,
)
from creativity_service.modules.iam.access import ENVIRONMENT_NAMES
from creativity_service.modules.iam.accounts import current_actor
from creativity_service.modules.iam.audit import append_event
from creativity_service.modules.iam.authorization import (
    effective_actions,
    platform_actions,
    require_platform,
)
from creativity_service.modules.iam.repositories import (
    membership_state,
    policy_key,
    to_state,
)
from creativity_service.modules.iam.repositories import (
    one as identity_one,
)
from creativity_service.modules.iam.repositories import (
    rows as identity_rows,
)
from creativity_service.modules.iam.roles import ACTION_NAMES
from creativity_service.modules.iam.schemas import AuditView, DirectoryPage
from creativity_service.modules.iam.services import IamServices

STATUS_LABELS = {"ACTIVE": "启用", "DISABLED": "停用", "SUSPENDED": "已暂停", "ARCHIVED": "已归档"}
BUSINESS_NAMES = {"gamerental": "租号", "playmate": "陪玩", "system": "平台系统"}
SERVICE_ACTIONS = frozenset(
    {
        "run:create",
        "run:read",
        "run:content",
        "analysis:run",
        "metric:read",
        "report:read",
        "conversation:read",
        "conversation:write",
        "memory:read",
        "memory:write",
        "memory:delete",
        "memory:preferences",
        "artifact:upload",
        "artifact:download",
        "content:derive",
        "snapshot:read",
        "data:export",
        "data:read_sensitive",
    }
)


def clean_name(value: str) -> str:
    value = value.strip()
    if not value:
        raise ServiceError("VALIDATION_ERROR", "名称不能为空", 422)
    return value


def capabilities(actions: list[str]) -> None:
    if not actions or len(actions) != len(set(actions)) or not set(actions) <= SERVICE_ACTIONS:
        raise ServiceError("INVALID_SCOPES", "可调用能力无效或重复", 422)
    if set(actions) & {
        "run:content",
        "conversation:read",
        "memory:read",
        "snapshot:read",
        "artifact:download",
    }:
        if "data:read_sensitive" not in actions:
            raise ServiceError("INVALID_SCOPES", "读取原文须明确授予敏感数据权限", 422)
    if "artifact:download" in actions and "data:export" not in actions:
        raise ServiceError("INVALID_SCOPES", "下载文件须明确授予导出权限", 422)


def mutable_values(body: Contract) -> dict[str, Any]:
    values = body.model_dump(exclude={"revision"}, exclude_unset=True)
    if not values or any(v is None for v in values.values()):
        raise ServiceError("VALIDATION_ERROR", "请提供有效的修改字段", 422)
    for field in ("name", "owner"):
        if field in values:
            values[field] = clean_name(values[field])
    return values


class ChannelService:
    def __init__(
        self,
        repository: ChannelRepository,
        iam: IamServices,
        *,
        usage: UsageReader | None = None,
        resources: ResourceReferenceReader | None = None,
    ) -> None:
        self.repository, self.iam = repository, iam
        self.usage_reader, self.resource_reader = usage, resources

    async def authorize(
        self, session: AdminSession, channel_id: str, action: str, *, credential: bool = False
    ) -> None:
        await self.iam.authentication.revalidate_admin(session, governance=True)
        if channel_id == "system":
            raise ServiceError("NOT_FOUND", "请求资源不存在", 404)
        if isinstance(session.context, AuthContext):
            if session.context.scope.channel_id != channel_id:
                raise ServiceError("NOT_FOUND", "请求资源不存在", 404)
            member = await self.iam.authentication.active_member(session.context)
            grants = await self.iam.authentication.identities.grants(channel_id)
            scope = session.context.scope
            if action not in effective_actions(
                member, grants, scope.environment, scope.data_scope_id or "", "channel", channel_id
            ):
                raise ServiceError("FORBIDDEN", "无权执行此操作", 403)
        else:
            require_platform(
                await self.iam.authentication.active_account(session.account.id), "channel:govern"
            )
            if credential:
                raise ServiceError("FORBIDDEN", "请进入获授权的渠道工作区管理接入凭据", 403)

    async def locked(
        self, uow: UnitOfWork, session: AdminSession, action: str, *, writable: bool = True
    ) -> dict[str, Any]:
        uow.require_lock(policy_key(uow.scope.channel_id))
        if isinstance(session.context, AuthContext):
            if session.context.scope.channel_id != uow.scope.channel_id:
                raise ServiceError("NOT_FOUND", "请求资源不存在", 404)
            await self.iam.access.locked_policy(uow, session, action)
            if action == "channel:manage":
                for domain in await rows(uow.connection, "data_scopes", uow.scope.channel_id):
                    await self.require_visible(
                        uow.connection,
                        session,
                        domain["environment"],
                        [domain["id"]],
                        action=action,
                    )
        else:
            await current_actor(uow, session, "channel:govern")
        channel = await required(
            uow.connection, "channels", uow.scope.channel_id, id=uow.scope.channel_id
        )
        if writable and channel["status"] == "ARCHIVED":
            raise ServiceError("INVALID_STATE", "已归档渠道不能修改", 409)
        return channel

    def scope(self, session: AdminSession, channel_id: str, environment: str = "dev") -> Scope:
        if isinstance(session.context, AuthContext):
            return session.context.scope
        # 此范围只服务治理事务，不会被签发为业务身份或进入业务内容仓储。
        return Scope.model_validate({"channel_id": channel_id, "environment": environment})

    def keys(self, channel_id: str, table: str, record_id: str, event_id: str) -> list[ResourceKey]:
        return [
            policy_key("system"),
            policy_key(channel_id),
            record_key(channel_id, table, record_id),
            record_key(channel_id, "audit_events", event_id),
            record_key(channel_id, "channel_lifecycle_events", event_id),
        ]

    async def visible(
        self,
        connection: AsyncConnection,
        session: AdminSession,
        environment: str,
        domains: list[str] | None = None,
        *,
        action: str | None = None,
        delegated: list[str] | None = None,
    ) -> bool:
        if not isinstance(session.context, AuthContext):
            return delegated is None
        scope = session.context.scope
        row = await identity_one(
            connection, "channel_memberships", scope.channel_id, user_id=session.account.id
        )
        if row is None or row["status"] != "ACTIVE" or environment not in row["environments"]:
            return False
        member = await membership_state(connection, row)
        target_domains = domains
        if target_domains is None:
            target_domains = [
                d["id"]
                for d in await rows(
                    connection, "data_scopes", scope.channel_id, environment=environment
                )
            ]
        if not target_domains:
            return False
        if not set(target_domains) <= set(member.data_scopes):
            return False
        if action or delegated:
            grants = [
                to_state(GrantState, r)
                for r in await identity_rows(connection, "resource_grants", scope.channel_id)
            ]
            needed = set(delegated or []) | ({action} if action else set())
            return all(
                needed
                <= effective_actions(member, grants, environment, d, "channel", scope.channel_id)
                for d in target_domains
            )
        return True

    async def require_visible(
        self,
        connection: AsyncConnection,
        session: AdminSession,
        environment: str,
        domains: list[str] | None = None,
        *,
        action: str | None = None,
        delegated: list[str] | None = None,
    ) -> None:
        if not await self.visible(
            connection, session, environment, domains, action=action, delegated=delegated
        ):
            raise ServiceError("NOT_FOUND", "请求资源不在授权范围内", 404)

    async def event(
        self,
        uow: UnitOfWork,
        event_id: str,
        session: AdminSession,
        action: str,
        target_type: str,
        row: dict[str, Any],
        previous: str | None = None,
        changed_fields: list[str] | None = None,
    ) -> None:
        if changed_fields is None:
            changed_fields = ["status"]
            if action.endswith(":create"):
                changed_fields += [
                    k
                    for k in (
                        "name",
                        "owner",
                        "channel_code",
                        "business_type",
                        "environment",
                        "release_policy",
                        "retention_policy",
                        "external_scope_type",
                        "external_scope_id",
                        "client_id",
                        "scopes",
                        "data_scopes",
                        "expires_at",
                    )
                    if k in row
                ]
            elif action == "key:rotate":
                changed_fields.append("expires_at")
        affected = await rows(uow.connection, "data_scopes", uow.scope.channel_id)
        if row.get("environment"):
            affected = [d for d in affected if d["environment"] == row["environment"]]
        domain_ids = None
        if target_type == "data_scope":
            domain_ids = [row["id"]]
        elif target_type == "client":
            domain_ids = row["data_scopes"]
        elif target_type == "key":
            client = await required(
                uow.connection, "service_clients", uow.scope.channel_id, id=row["client_id"]
            )
            domain_ids = client["data_scopes"]
        if domain_ids is not None:
            affected = [d for d in affected if d["id"] in domain_ids]
        await append_event(
            uow,
            event_id,
            session.account.id,
            session.context.request_id,
            action,
            target_type,
            row["id"],
            changed_fields,
            affected_scopes=[
                {"environment": d["environment"], "data_scope_id": d["id"]} for d in affected
            ]
            or None,
        )
        await save(
            uow,
            "channel_lifecycle_events",
            event_id,
            {
                "event_type": action,
                "target_type": target_type,
                "target_id": row["id"],
                "environment": row.get("environment"),
                "payload": {
                    "previous_status": previous,
                    "current_status": row["status"],
                    "revision": row["revision"],
                },
                "acknowledgements": {},
            },
        )

    async def initialize_system(self) -> None:
        scope = ControlScope(purpose="channel_directory", actor_id="channel_initializer")
        async with transaction(
            self.repository.engine,
            scope,
            [policy_key("system"), record_key("system", "channels", "system")],
        ) as uow:
            if await one(uow.connection, "channels", "system", id="system"):
                return
            await save(
                uow,
                "channels",
                "system",
                system_channel_values(),
            )

    async def create(self, session: AdminSession, body: ChannelCreate) -> ChannelView:
        await self.iam.authentication.revalidate_admin(session)
        if isinstance(session.context, AuthContext):
            raise ServiceError("FORBIDDEN", "开通渠道须使用平台管理会话", 403)
        require_platform(session.account, "channel:create")
        name = clean_name(body.name)
        code = channel_code(name)
        self.validate_mapping(
            body.data_scope.external_scope_type,
            body.data_scope.external_scope_id,
        )
        channel_id, domain_id, index_id, event_id = (
            new_id("channel"),
            new_id("scope"),
            new_id("code"),
            new_id("audit"),
        )
        env_id = environment_id(channel_id, body.environment)
        scope = Scope(channel_id=channel_id, environment=body.environment, data_scope_id=domain_id)
        keys = (
            self.keys(channel_id, "channels", channel_id, event_id)
            + self.iam.access.provisioning_keys(channel_id, body.first_admin_user_id)
            + RecoveryService.keys(scope)
            + [
                record_key(channel_id, "channel_environments", env_id),
                record_key(channel_id, "data_scopes", domain_id),
                record_key("system", "channel_code_index", index_id),
                ResourceKey("system", "channel_code_index", (code,)),
                mapping_key(
                    channel_id,
                    body.environment,
                    body.data_scope.external_scope_type,
                    body.data_scope.external_scope_id,
                ),
            ]
        )
        async with transaction(self.repository.engine, scope, keys) as uow:
            await current_actor(uow, session, "channel:create")
            if await one(uow.connection, "channels", "system", id="system") is None:
                raise unavailable("系统渠道初始化")
            await self.repository.add_index(
                uow,
                "channel_code_index",
                index_id,
                {"channel_code": code, "target_channel_id": channel_id},
            )
            row = await save(
                uow,
                "channels",
                channel_id,
                {
                    "channel_code": code,
                    "name": name,
                    "owner": clean_name(body.owner),
                    "business_type": clean_name(body.business_type) if body.business_type else None,
                    "status": "ACTIVE",
                    "archived_at": None,
                    "retention_policy": body.retention_policy.model_dump(),
                    "budget_policy_refs": [],
                    "rate_limit_policy_refs": [],
                },
            )
            await save(
                uow,
                "channel_environments",
                env_id,
                {
                    "environment": body.environment,
                    "name": ENVIRONMENT_NAMES[body.environment],
                    "status": "ACTIVE",
                    "release_policy": {"approval_required": True},
                    "retention_policy": body.retention_policy.model_dump(),
                },
            )
            await save(
                uow,
                "data_scopes",
                domain_id,
                {
                    **body.data_scope.model_dump(),
                    "name": clean_name(body.data_scope.name),
                    "environment": body.environment,
                    "status": "ACTIVE",
                },
            )
            await RecoveryService.initialize_fresh_in(uow, scope)
            await self.iam.access.provision_first_member(
                uow,
                session,
                body.first_admin_user_id,
                [body.environment],
                [domain_id],
                list(body.independent_actions),
            )
            await self.event(uow, event_id, session, "channel:create", "channel", row)
        return self.channel_view(row, session)

    @staticmethod
    def validate_mapping(kind: str, external_id: str) -> None:
        # 映射是源系统的精确身份，不规范化、不补默认值，避免不同编号合并授权。
        if any(
            not value
            or value != value.strip()
            or any(unicodedata.category(c) == "Cc" for c in value)
            for value in (kind, external_id)
        ):
            raise ServiceError(
                "INVALID_MAPPING", "外部数据域类型和编号不能为空或含首尾空白、控制字符", 422
            )

    def channel_view(
        self, row: dict[str, Any], session: AdminSession, actions: set[str] | None = None
    ) -> ChannelView:
        allowed = actions if actions is not None else set(platform_actions(session.account))
        return ChannelView(
            channel_id=row["id"],
            channel_code=row["channel_code"],
            name=row["name"],
            owner=row["owner"],
            business_type=row["business_type"],
            business_type_name=BUSINESS_NAMES.get(row["business_type"], row["business_type"]),
            status=row["status"],
            status_label=STATUS_LABELS[row["status"]],
            created_at=row["created_at"],
            archived_at=row["archived_at"],
            retention_policy=row["retention_policy"],
            revision=row["revision"],
            actions=[
                VisibleAction(action_key=a, label=ACTION_NAMES[a])
                for a in sorted(allowed)
                if a
                in {
                    "channel:govern",
                    "channel:manage",
                    "environment:manage",
                    "data_scope:manage",
                    "client:manage",
                    "key:manage",
                    "membership:read",
                    "audit:read",
                    "usage:read",
                }
            ],
        )

    async def detail(self, session: AdminSession, channel_id: str) -> ChannelView:
        await self.authorize(session, channel_id, "channel:manage")
        async with self.repository.engine.connect() as connection:
            row = await required(connection, "channels", channel_id, id=channel_id)
        actions = None
        if isinstance(session.context, AuthContext):
            member = await self.iam.authentication.active_member(session.context)
            actions = set(
                effective_actions(
                    member,
                    await self.iam.authentication.identities.grants(channel_id),
                    session.context.scope.environment,
                    session.context.scope.data_scope_id or "",
                    "channel",
                    channel_id,
                )
            )
        return self.channel_view(row, session, actions)

    async def list_items(
        self,
        session: AdminSession,
        limit: int = 100,
        offset: int = 0,
        search: str = "",
        status: str | None = None,
    ) -> list[ChannelView]:
        return (await self.list_page(session, limit, offset, search, status)).items

    async def list_page(
        self,
        session: AdminSession,
        limit: int = 20,
        offset: int = 0,
        search: str = "",
        status: str | None = None,
    ) -> DirectoryPage[ChannelView]:
        await self.iam.authentication.revalidate_admin(session, governance=True)
        if (
            not 1 <= limit <= 200
            or offset < 0
            or status not in {None, "ACTIVE", "SUSPENDED", "ARCHIVED"}
        ):
            raise ServiceError("VALIDATION_ERROR", "分页或状态条件不正确", 422)
        if isinstance(session.context, AuthContext):
            row = await self.detail(session, session.context.scope.channel_id)
            matched = (not search or search in row.name) and (not status or row.status == status)
            return DirectoryPage(
                items=[row] if matched and offset == 0 else [],
                total=int(matched),
                offset=offset,
                limit=limit,
            )
        require_platform(session.account, "channel:govern")
        async with self.repository.engine.connect() as connection:
            result, total = await self.repository.directory_page(
                connection, limit, offset, search, status
            )
        return DirectoryPage(
            items=[self.channel_view(r, session) for r in result],
            total=total,
            offset=offset,
            limit=limit,
        )

    async def update(
        self, session: AdminSession, channel_id: str, body: ChannelUpdate
    ) -> ChannelView:
        await self.authorize(session, channel_id, "channel:manage")
        event_id = new_id("audit")
        async with transaction(
            self.repository.engine,
            self.scope(session, channel_id),
            self.keys(channel_id, "channels", channel_id, event_id),
        ) as uow:
            previous = await self.locked(uow, session, "channel:manage")
            row = await save(uow, "channels", channel_id, mutable_values(body), body.revision)
            await self.event(
                uow,
                event_id,
                session,
                "channel:update",
                "channel",
                row,
                previous["status"],
                changed_fields=sorted(body.model_fields_set - {"revision"}),
            )
        return await self.detail(session, channel_id)

    async def environments(self, session: AdminSession, channel_id: str) -> list[EnvironmentView]:
        await self.authorize(session, channel_id, "environment:manage")
        async with self.repository.engine.connect() as connection:
            await required(connection, "channels", channel_id, id=channel_id)
            data = await ChannelReadData.load(connection, session, channel_id)
            result = [
                r
                for r in data.environments.values()
                if data.visible(r["environment"], action="environment:manage")
            ]
        return [self.environment_view(r) for r in result]

    @staticmethod
    def environment_view(row: dict[str, Any]) -> EnvironmentView:
        return EnvironmentView(
            **{k: row[k] for k in EnvironmentView.model_fields if k != "status_label"},
            status_label=STATUS_LABELS[row["status"]],
        )

    async def create_environment(
        self, session: AdminSession, channel_id: str, body: EnvironmentCreate
    ) -> EnvironmentView:
        await self.authorize(session, channel_id, "environment:manage")
        env_id, event_id = environment_id(channel_id, body.environment), new_id("audit")
        async with transaction(
            self.repository.engine,
            self.scope(session, channel_id, body.environment),
            self.keys(channel_id, "channel_environments", env_id, event_id),
        ) as uow:
            await self.locked(uow, session, "environment:manage")
            if await one(
                uow.connection, "channel_environments", channel_id, environment=body.environment
            ):
                raise ServiceError("ENVIRONMENT_EXISTS", "环境编码已存在", 409)
            row = await save(
                uow,
                "channel_environments",
                env_id,
                {**body.model_dump(), "name": clean_name(body.name), "status": "ACTIVE"},
            )
            await self.event(uow, event_id, session, "environment:create", "environment", row)
        return self.environment_view(row)

    async def update_environment(
        self, session: AdminSession, channel_id: str, environment: str, body: EnvironmentUpdate
    ) -> EnvironmentView:
        await self.authorize(session, channel_id, "environment:manage")
        env_id, event_id = environment_id(channel_id, environment), new_id("audit")
        async with transaction(
            self.repository.engine,
            self.scope(session, channel_id),
            self.keys(channel_id, "channel_environments", env_id, event_id),
        ) as uow:
            await self.locked(uow, session, "environment:manage")
            previous = await required(
                uow.connection, "channel_environments", channel_id, environment=environment
            )
            await self.require_visible(
                uow.connection, session, environment, action="environment:manage"
            )
            row = await save(
                uow, "channel_environments", env_id, mutable_values(body), body.revision
            )
            await self.event(
                uow,
                event_id,
                session,
                "environment:update",
                "environment",
                row,
                previous["status"],
                changed_fields=sorted(body.model_fields_set - {"revision"}),
            )
        return self.environment_view(row)

    async def data_scopes(self, session: AdminSession, channel_id: str) -> list[DataScopeView]:
        await self.authorize(session, channel_id, "data_scope:manage")
        async with self.repository.engine.connect() as connection:
            await required(connection, "channels", channel_id, id=channel_id)
            data = await ChannelReadData.load(connection, session, channel_id)
            return [
                await self.domain_view(connection, r, data)
                for r in data.domains.values()
                if data.visible(r["environment"], [r["id"]], action="data_scope:manage")
            ]

    async def domain_view(
        self, connection: AsyncConnection, row: dict[str, Any], data: ChannelReadData | None = None
    ) -> DataScopeView:
        env = (
            data.environments.get(row["environment"])
            if data is not None
            else await one(
                connection,
                "channel_environments",
                row["channel_id"],
                environment=row["environment"],
            )
        )
        return DataScopeView(
            data_scope_id=row["id"],
            environment_name=env["name"] if env else None,
            external_scope_type_name={"club": "俱乐部", "default": "默认业务域"}.get(
                row["external_scope_type"]
            ),
            status_label=STATUS_LABELS[row["status"]],
            **{
                k: row[k]
                for k in (
                    "environment",
                    "name",
                    "external_scope_type",
                    "external_scope_id",
                    "status",
                    "revision",
                )
            },
        )

    async def create_data_scope(
        self, session: AdminSession, channel_id: str, body: DataScopeCreate
    ) -> DataScopeView:
        await self.authorize(session, channel_id, "data_scope:manage")
        domain_id, event_id = new_id("scope"), new_id("audit")
        fresh_scope = Scope(
            channel_id=channel_id, environment=body.environment, data_scope_id=domain_id
        )
        keys = (
            RecoveryService.keys(fresh_scope)
            + self.keys(channel_id, "data_scopes", domain_id, event_id)
            + [
                mapping_key(
                    channel_id, body.environment, body.external_scope_type, body.external_scope_id
                )
            ]
        )
        if body.administrator_id:
            if isinstance(session.context, AuthContext):
                raise ServiceError("FORBIDDEN", "新工作区管理员须由平台治理入口明确授权", 403)
            keys += self.iam.access.workspace_provisioning_keys(
                channel_id, body.administrator_id, domain_id
            )
        async with transaction(self.repository.engine, fresh_scope, keys) as uow:
            await self.locked(uow, session, "data_scope:manage")
            env = await required(
                uow.connection, "channel_environments", channel_id, environment=body.environment
            )
            if env["status"] != "ACTIVE":
                raise ServiceError("ENVIRONMENT_DISABLED", "环境已停用", 403)
            self.validate_mapping(body.external_scope_type, body.external_scope_id)
            if await one(
                uow.connection,
                "data_scopes",
                channel_id,
                environment=body.environment,
                external_scope_type=body.external_scope_type,
                external_scope_id=body.external_scope_id,
            ):
                raise ServiceError("MAPPING_EXISTS", "此环境的业务数据域映射已存在", 409)
            row = await save(
                uow,
                "data_scopes",
                domain_id,
                {
                    **body.model_dump(exclude={"administrator_id"}),
                    "name": clean_name(body.name),
                    "status": "ACTIVE",
                },
            )
            await RecoveryService.initialize_fresh_in(uow, fresh_scope)
            if body.administrator_id:
                await self.iam.access.provision_workspace(
                    uow, session, body.administrator_id, body.environment, domain_id
                )
            await self.event(uow, event_id, session, "data_scope:create", "data_scope", row)
            return await self.domain_view(uow.connection, row)

    async def update_data_scope(
        self, session: AdminSession, channel_id: str, domain_id: str, body: DataScopeUpdate
    ) -> DataScopeView:
        await self.authorize(session, channel_id, "data_scope:manage")
        event_id = new_id("audit")
        async with transaction(
            self.repository.engine,
            self.scope(session, channel_id),
            self.keys(channel_id, "data_scopes", domain_id, event_id),
        ) as uow:
            await self.locked(uow, session, "data_scope:manage")
            previous = await required(uow.connection, "data_scopes", channel_id, id=domain_id)
            await self.require_visible(
                uow.connection,
                session,
                previous["environment"],
                [domain_id],
                action="data_scope:manage",
            )
            row = await save(uow, "data_scopes", domain_id, mutable_values(body), body.revision)
            await self.event(
                uow,
                event_id,
                session,
                "data_scope:update",
                "data_scope",
                row,
                previous["status"],
                changed_fields=sorted(body.model_fields_set - {"revision"}),
            )
            return await self.domain_view(uow.connection, row)

    async def validate_client(
        self,
        connection: AsyncConnection,
        session: AdminSession,
        channel_id: str,
        values: dict[str, Any],
    ) -> None:
        capabilities(values["scopes"])
        if len(values["data_scopes"]) != len(set(values["data_scopes"])):
            raise ServiceError("INVALID_SCOPES", "数据域不能重复", 422)
        env = await required(
            connection, "channel_environments", channel_id, environment=values["environment"]
        )
        if env["status"] != "ACTIVE":
            raise ServiceError("ENVIRONMENT_DISABLED", "环境已停用", 403)
        for domain_id in values["data_scopes"]:
            domain = await required(
                connection,
                "data_scopes",
                channel_id,
                id=domain_id,
                environment=values["environment"],
            )
            if domain["status"] != "ACTIVE":
                raise ServiceError("DATA_SCOPE_DISABLED", "业务数据域已停用", 403)
        await self.require_visible(
            connection,
            session,
            values["environment"],
            values["data_scopes"],
            action="client:manage",
            delegated=values["scopes"],
        )

    async def clients(self, session: AdminSession, channel_id: str) -> list[ClientView]:
        await self.authorize(session, channel_id, "client:manage")
        async with self.repository.engine.connect() as connection:
            await required(connection, "channels", channel_id, id=channel_id)
            data = await ChannelReadData.load(connection, session, channel_id)
            return [
                await self.client_view(connection, r, data)
                for r in data.clients.values()
                if data.visible(r["environment"], r["data_scopes"], action="client:manage")
            ]

    async def client_view(
        self, connection: AsyncConnection, row: dict[str, Any], data: ChannelReadData | None = None
    ) -> ClientView:
        env = (
            data.environments.get(row["environment"])
            if data is not None
            else await one(
                connection,
                "channel_environments",
                row["channel_id"],
                environment=row["environment"],
            )
        )
        domains = {
            d["id"]: d["name"]
            for d in (
                data.domains.values()
                if data is not None
                else await rows(
                    connection, "data_scopes", row["channel_id"], environment=row["environment"]
                )
            )
        }
        return ClientView(
            client_id=row["id"],
            environment_name=env["name"] if env else None,
            data_scope_names=[domains.get(d) for d in row["data_scopes"]],
            scope_names=[ACTION_NAMES[a] for a in row["scopes"]],
            status_label=STATUS_LABELS[row["status"]],
            **{
                k: row[k]
                for k in ("name", "environment", "scopes", "data_scopes", "status", "revision")
            },
        )

    async def create_client(
        self, session: AdminSession, channel_id: str, body: ClientCreate
    ) -> ClientView:
        await self.authorize(session, channel_id, "client:manage", credential=True)
        client_id, event_id = new_id("client"), new_id("audit")
        async with transaction(
            self.repository.engine,
            self.scope(session, channel_id),
            self.keys(channel_id, "service_clients", client_id, event_id),
        ) as uow:
            await self.locked(uow, session, "client:manage")
            await self.validate_client(uow.connection, session, channel_id, body.model_dump())
            row = await save(
                uow,
                "service_clients",
                client_id,
                {**body.model_dump(), "name": clean_name(body.name), "status": "ACTIVE"},
            )
            await self.event(uow, event_id, session, "client:create", "client", row)
            return await self.client_view(uow.connection, row)

    async def update_client(
        self, session: AdminSession, channel_id: str, client_id: str, body: ClientUpdate
    ) -> ClientView:
        await self.authorize(session, channel_id, "client:manage", credential=True)
        event_id = new_id("audit")
        async with transaction(
            self.repository.engine,
            self.scope(session, channel_id),
            self.keys(channel_id, "service_clients", client_id, event_id),
        ) as uow:
            await self.locked(uow, session, "client:manage")
            previous = await required(uow.connection, "service_clients", channel_id, id=client_id)
            await self.require_visible(
                uow.connection,
                session,
                previous["environment"],
                previous["data_scopes"],
                action="client:manage",
            )
            values = mutable_values(body)
            if set(values) & {"scopes", "data_scopes"} or values.get("status") == "ACTIVE":
                await self.validate_client(
                    uow.connection, session, channel_id, {**previous, **values}
                )
            row = await save(uow, "service_clients", client_id, values, body.revision)
            await self.event(
                uow,
                event_id,
                session,
                "client:update",
                "client",
                row,
                previous["status"],
                changed_fields=sorted(body.model_fields_set - {"revision"}),
            )
            return await self.client_view(uow.connection, row)

    async def overview(self, session: AdminSession, channel_id: str) -> OverviewView:
        await self.authorize(session, channel_id, "channel:manage")
        async with self.repository.engine.connect() as connection:
            row = await required(connection, "channels", channel_id, id=channel_id)
            data = await ChannelReadData.load(connection, session, channel_id)
            allowed = None
            if isinstance(session.context, AuthContext):
                scope = session.context.scope
                allowed = (
                    set(
                        effective_actions(
                            data.member,
                            data.grants,
                            scope.environment,
                            scope.data_scope_id or "",
                            "channel",
                            channel_id,
                        )
                    )
                    if data.member
                    else set()
                )
                if (
                    not {
                        "channel:manage",
                        "environment:manage",
                        "data_scope:manage",
                        "client:manage",
                    }
                    <= allowed
                ):
                    raise ServiceError("FORBIDDEN", "无权查看渠道概览", 403)
            channel = self.channel_view(row, session, allowed)
            envs = [
                r
                for r in data.environments.values()
                if data.visible(r["environment"], action="environment:manage")
            ]
            domains = [
                r
                for r in data.domains.values()
                if data.visible(r["environment"], [r["id"]], action="data_scope:manage")
            ]
            clients = {
                r["id"]
                for r in data.clients.values()
                if data.visible(r["environment"], r["data_scopes"], action="client:manage")
            }
            keys = [
                r
                for r in await rows(connection, "channel_keys", channel_id, status="ACTIVE")
                if r["expires_at"] > utcnow() and r["client_id"] in clients
            ]
        references = None
        if self.resource_reader:
            references = await self.resource_reader.references(session, channel_id)
        return OverviewView(
            channel=channel,
            environments=len(envs),
            data_scopes=len(domains),
            clients=len(clients),
            active_keys=len(keys),
            members_path=f"/admin/v1/channels/{channel_id}/members",
            audit_path=f"/admin/v1/channels/{channel_id}/audit-events",
            resource_references=references,
        )

    async def usage(self, session: AdminSession, channel_id: str, query: UsageQuery) -> UsageView:
        if isinstance(session.context, AuthContext):
            await self.authorize(session, channel_id, "usage:read")
        else:
            await self.iam.authentication.revalidate_admin(session, governance=True)
            require_platform(session.account, "usage:platform")
        if query.start_at >= query.end_at:
            raise ServiceError("VALIDATION_ERROR", "结束时间须晚于开始时间", 422)
        async with self.repository.engine.connect() as connection:
            await required(connection, "channels", channel_id, id=channel_id)
            data = await ChannelReadData.load(connection, session, channel_id)
            scopes = [
                Scope(channel_id=channel_id, environment=d["environment"], data_scope_id=d["id"])
                for d in data.domains.values()
                if data.visible(d["environment"], [d["id"]], action="usage:read")
            ]
        if not scopes:
            raise ServiceError("FORBIDDEN", "没有可查询的用量范围", 403)
        if self.usage_reader is None:
            raise unavailable("渠道用量查询服务")
        result = await self.usage_reader.query(channel_id, scopes, query)
        if (
            result.channel_id != channel_id
            or result.start_at != query.start_at
            or result.end_at != query.end_at
        ):
            raise ServiceError("SCOPE_MISMATCH", "用量查询归属不符", 503)
        return result

    async def audit(
        self, session: AdminSession, channel_id: str, limit: int = 50
    ) -> list[AuditView]:
        from creativity_service.modules.iam.schemas import AuditFilter

        return (await self.iam.audit.page(session, AuditFilter(limit=limit), channel_id)).items

    async def platform_usage(
        self, session: AdminSession, channel_ids: list[str], start_at: str, end_at: str
    ) -> list[UsageView]:
        from pydantic import ValidationError

        await self.iam.authentication.revalidate_admin(session, governance=True)
        require_platform(session.account, "usage:platform")
        if isinstance(session.context, AuthContext):
            raise ServiceError("FORBIDDEN", "跨渠道统计须使用平台管理会话", 403)
        if not channel_ids or len(set(channel_ids)) != len(channel_ids) or "system" in channel_ids:
            raise ServiceError("VALIDATION_ERROR", "须明确指定不重复的业务渠道范围", 422)
        try:
            query = UsageQuery(start_at=start_at, end_at=end_at)
        except ValidationError as exc:
            raise ServiceError("VALIDATION_ERROR", "查询时间格式不正确", 422) from exc
        result = [await self.usage(session, c, query) for c in channel_ids]
        event_id = new_id("audit")
        async with transaction(
            self.repository.engine,
            ControlScope(purpose="audit", actor_id=session.account.id),
            [record_key("system", "audit_events", event_id)],
        ) as uow:
            await append_event(
                uow,
                event_id,
                session.account.id,
                session.context.request_id,
                "usage:platform",
                "channel",
                "selected_channels",
                channel_ids=channel_ids,
            )
        return result

    async def usage_channels(
        self, session: AdminSession, search: str, offset: int, limit: int
    ) -> DirectoryPage[dict[str, str]]:
        await self.iam.authentication.revalidate_admin(session, governance=True)
        if isinstance(session.context, AuthContext):
            raise ServiceError("FORBIDDEN", "平台用量须使用平台工作区", 403)
        require_platform(session.account, "usage:platform")
        if not 1 <= limit <= 200 or offset < 0:
            raise ServiceError("VALIDATION_ERROR", "分页条件不正确", 422)
        async with self.repository.engine.connect() as connection:
            records, total = await self.repository.directory_page(connection, limit, offset, search)
        return DirectoryPage(
            items=[{"value": r["id"], "label": r["name"]} for r in records],
            total=total,
            offset=offset,
            limit=limit,
        )
