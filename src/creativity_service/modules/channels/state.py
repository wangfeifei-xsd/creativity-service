"""生产当前状态读取器，Token 与后台执行边界共用真实渠道记录。"""

from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncConnection

from creativity_service.core.auth.types import (
    GrantState,
    MembershipState,
    ResourceState,
    ServiceIdentity,
    WorkspaceOption,
)
from creativity_service.core.context import AuthContext, ChannelState, Scope
from creativity_service.core.database.reading import read_connection
from creativity_service.core.primitives import ServiceError, utcnow
from creativity_service.modules.channels.repositories import (
    ChannelRepository,
    one,
    rows,
    scope_rows,
)
from creativity_service.modules.channels.tables import metadata
from creativity_service.modules.iam.authorization import effective_actions
from creativity_service.modules.iam.repositories import TABLES as identity_tables
from creativity_service.modules.iam.repositories import (
    membership_from_catalog,
    role_catalog_rows,
    to_state,
)
from creativity_service.modules.iam.repositories import one as identity_one
from creativity_service.modules.iam.roles import GOVERNANCE_ACTIONS


def require_available(channel: dict[str, Any], environment: dict[str, Any]) -> None:
    if channel["status"] == "SUSPENDED":
        raise ServiceError("CHANNEL_SUSPENDED", "渠道已暂停", 403)
    if channel["status"] == "ARCHIVED":
        raise ServiceError("CHANNEL_ARCHIVED", "渠道已归档", 403)
    if channel["status"] != "ACTIVE" or environment["status"] != "ACTIVE":
        raise ServiceError("ENVIRONMENT_DISABLED", "环境已停用", 403)


async def current_service(connection: AsyncConnection, context: AuthContext) -> ServiceIdentity:
    scope = context.scope
    key = await one(connection, "channel_keys", scope.channel_id, id=context.key_id)
    if key is None or key["status"] != "ACTIVE" or key["expires_at"] <= utcnow():
        raise ServiceError("KEY_REVOKED", "接入凭据无效或已到期", 401)
    client = await one(connection, "service_clients", scope.channel_id, id=key["client_id"])
    if (
        client is None
        or client["status"] != "ACTIVE"
        or client["id"] != context.client_id
        or client["id"] != context.principal_id
        or client["environment"] != scope.environment
        or key["environment"] != scope.environment
    ):
        raise ServiceError("CLIENT_REVOKED", "接入服务不可用", 401)
    scopes = frozenset(
        row["id"]
        for row in await rows(
            connection,
            "data_scopes",
            scope.channel_id,
            environment=scope.environment,
            status="ACTIVE",
        )
        if row["id"] in client["data_scopes"]
    )
    if not scopes:
        raise ServiceError("DATA_SCOPE_DISABLED", "接入服务没有可用数据域", 403)
    if scope.data_scope_id and scope.data_scope_id not in scopes:
        raise ServiceError("NOT_FOUND", "请求资源不存在", 404)
    if not set(key["scopes"]) & set(client["scopes"]):
        raise ServiceError("CLIENT_REVOKED", "接入服务已无可调用权限", 401)
    return ServiceIdentity(
        channel_id=scope.channel_id,
        environment=scope.environment,
        client_id=client["id"],
        key_id=key["id"],
        expires_at=key["expires_at"],
        client_actions=frozenset(client["scopes"]),
        key_actions=frozenset(key["scopes"]),
        data_scopes=scopes,
    )


class ChannelStateService:
    def __init__(self, repository: ChannelRepository) -> None:
        self.repository = repository

    async def read_current(self, context: AuthContext) -> ChannelState:
        return await self.read_validated(context)

    async def read_validated(
        self,
        context: AuthContext,
        *,
        member: MembershipState | None = None,
        service: ServiceIdentity | None = None,
    ) -> ChannelState:
        """认证链可传入本次已核对的身份；独立状态查询仍读取当前身份。"""
        scope = context.scope
        if member and (member.channel_id != scope.channel_id or member.user_id != context.actor_id):
            raise ServiceError("SCOPE_MISMATCH", "成员身份与当前范围不符", 403)
        if service and (
            service.channel_id != scope.channel_id
            or service.environment != scope.environment
            or service.client_id != context.client_id
            or service.key_id != context.key_id
        ):
            raise ServiceError("SCOPE_MISMATCH", "接入身份与当前范围不符", 403)
        async with read_connection(self.repository.engine) as connection:
            current = await scope_rows(connection, scope)
            channel, environment = current["channels"], current["channel_environments"]
            domain = current.get("data_scopes")
            stored_member = (
                await identity_one(
                    connection, "channel_memberships", scope.channel_id, user_id=context.actor_id
                )
                if context.actor_id and member is None
                else None
            )
            identity = service or (
                await current_service(connection, context) if context.client_id else None
            )
            # 管理检查可读取暂停状态；业务认证由公共状态门禁拒绝继续执行。
            if context.client_id:
                require_available(channel, environment)
            return ChannelState(
                channel_id=scope.channel_id,
                environment=scope.environment,
                channel_active=channel["status"] == "ACTIVE",
                environment_active=environment["status"] == "ACTIVE",
                membership_active=(
                    member.status == "ACTIVE"
                    if member
                    else stored_member["status"] == "ACTIVE"
                    if stored_member
                    else None
                ),
                client_active=bool(identity) if context.client_id else None,
                key_active=bool(identity) if context.key_id else None,
                data_scope_active=domain["status"] == "ACTIVE" if domain else None,
                channel_status=channel["status"],
            )


class ServiceIdentityService:
    def __init__(self, repository: ChannelRepository) -> None:
        self.repository = repository

    async def read_current(self, context: AuthContext) -> ServiceIdentity:
        async with self.repository.engine.connect() as connection:
            return await current_service(connection, context)


class ChannelDirectory:
    def __init__(self, repository: ChannelRepository) -> None:
        self.repository = repository

    async def data_for(self, user_id: str) -> dict[str, list[dict[str, Any]]]:
        async with self.repository.engine.connect() as connection:
            located = await self.repository.directory(connection)
            members = identity_tables["channel_memberships"]
            memberships = [
                dict(r)
                for r in (
                    await connection.execute(
                        select(members).where(
                            members.c.channel_id.in_(located),
                            members.c.user_id == user_id,
                            members.c.status == "ACTIVE",
                        )
                    )
                ).mappings()
            ]
            identifiers = {r["channel_id"] for r in memberships}
            if len(identifiers) != len(memberships):
                raise ServiceError("STORAGE_INVARIANT_BROKEN", "渠道成员身份重复", 503)
            result = {"members": memberships}
            # 只有已定位且有真实成员关系的渠道进入批量读取，系统渠道不是通配符。
            for name in (
                "channels",
                "channel_environments",
                "data_scopes",
                "resource_grants",
                "custom_roles",
            ):
                table = metadata.tables[name] if name in metadata.tables else identity_tables[name]
                result[name] = (
                    [
                        dict(r)
                        for r in (
                            await connection.execute(
                                select(table).where(table.c.channel_id.in_(identifiers))
                            )
                        ).mappings()
                    ]
                    if identifiers
                    else []
                )
        return result

    async def options_for(self, user_id: str, *, authorized: bool) -> list[WorkspaceOption]:
        data = await self.data_for(user_id)
        members = {r["channel_id"]: r for r in data["members"]}
        channels = {r["channel_id"]: r for r in data["channels"] if r["id"] == r["channel_id"]}
        environments = {
            (r["channel_id"], r["environment"]): r for r in data["channel_environments"]
        }
        grants = {
            identifier: [
                to_state(GrantState, r)
                for r in data["resource_grants"]
                if r["channel_id"] == identifier
            ]
            for identifier in members
        }
        states = {
            identifier: membership_from_catalog(
                row,
                role_catalog_rows(
                    [r for r in data["custom_roles"] if r["channel_id"] == identifier]
                ),
            )
            for identifier, row in members.items()
        }
        result = []
        for domain in data["data_scopes"]:
            identifier = domain["channel_id"]
            member, channel = states[identifier], channels.get(identifier)
            env = environments.get((identifier, domain["environment"]))
            if (
                not channel
                or channel["status"] == "ARCHIVED"
                or not env
                or env["environment"] not in member.environments
                or domain["id"] not in member.data_scopes
            ):
                continue
            if authorized:
                available = grants[identifier]
                if not any(
                    effective_actions(
                        member,
                        available,
                        env["environment"],
                        domain["id"],
                        g.resource_type,
                        g.resource_id,
                    )
                    for g in available
                ):
                    continue
                governed = bool(
                    effective_actions(
                        member, available, env["environment"], domain["id"], "channel", identifier
                    )
                    & GOVERNANCE_ACTIONS
                )
                if not governed and (
                    channel["status"] != "ACTIVE"
                    or env["status"] != "ACTIVE"
                    or domain["status"] != "ACTIVE"
                ):
                    continue
            result.append(
                WorkspaceOption(
                    channel_id=identifier,
                    channel_name=channel["name"],
                    environment=env["environment"],
                    environment_name=env["name"],
                    data_scope_id=domain["id"],
                    data_scope_name=domain["name"],
                )
            )
        return result

    async def list_for(self, user_id: str) -> list[WorkspaceOption]:
        return await self.options_for(user_id, authorized=False)

    async def authorized_for(self, user_id: str) -> list[WorkspaceOption]:
        return await self.options_for(user_id, authorized=True)


class ChannelResourceReader:
    display_tables = {
        "channel": ["channels"],
        "environment": ["channel_environments"],
        "data_scope": ["data_scopes"],
        "client": ["service_clients"],
        "key": ["channel_keys"],
    }

    def __init__(self, repository: ChannelRepository) -> None:
        self.repository = repository

    async def read_current(
        self, context: AuthContext, resource_type: str, resource_id: str
    ) -> ResourceState | None:
        table = {
            "channel": "channels",
            "environment": "channel_environments",
            "data_scope": "data_scopes",
            "client": "service_clients",
            "key": "channel_keys",
        }.get(resource_type)
        if table is None:
            return None
        scope = context.scope
        async with self.repository.engine.connect() as connection:
            value = await one(connection, table, scope.channel_id, id=resource_id)
        if value is None or ("environment" in value and value["environment"] != scope.environment):
            return None
        if resource_type == "data_scope" and value["id"] != scope.data_scope_id:
            return None
        return ResourceState(
            scope=Scope(
                channel_id=scope.channel_id,
                environment=scope.environment,
                data_scope_id=value["id"] if resource_type == "data_scope" else None,
            ),
            resource_type=resource_type,
            resource_id=resource_id,
            name=value["name"],
            active=value["status"] == "ACTIVE",
        )
