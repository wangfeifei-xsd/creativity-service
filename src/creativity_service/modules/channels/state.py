"""生产当前状态读取器，Token 与后台执行边界共用真实渠道记录。"""

from typing import Any

from sqlalchemy.ext.asyncio import AsyncConnection

from creativity_service.core.auth.types import ResourceState, ServiceIdentity, WorkspaceOption
from creativity_service.core.context import AuthContext, ChannelState, Scope
from creativity_service.core.primitives import ServiceError, utcnow
from creativity_service.modules.channels.repositories import ChannelRepository, one, required, rows
from creativity_service.modules.iam.repositories import one as identity_one


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
        scope = context.scope
        async with self.repository.engine.connect() as connection:
            channel = await required(connection, "channels", scope.channel_id, id=scope.channel_id)
            environment = await required(
                connection, "channel_environments", scope.channel_id, environment=scope.environment
            )
            member = (
                await identity_one(
                    connection, "channel_memberships", scope.channel_id, user_id=context.actor_id
                )
                if context.actor_id
                else None
            )
            domain = (
                await required(
                    connection,
                    "data_scopes",
                    scope.channel_id,
                    id=scope.data_scope_id,
                    environment=scope.environment,
                )
                if scope.data_scope_id
                else None
            )
            identity = await current_service(connection, context) if context.client_id else None
            # 管理检查可读取暂停状态；业务认证由公共状态门禁拒绝继续执行。
            if context.client_id:
                require_available(channel, environment)
            return ChannelState(
                channel_id=scope.channel_id,
                environment=scope.environment,
                channel_active=channel["status"] == "ACTIVE",
                environment_active=environment["status"] == "ACTIVE",
                membership_active=member["status"] == "ACTIVE" if member else None,
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

    async def list_for(self, user_id: str) -> list[WorkspaceOption]:
        result = []
        async with self.repository.engine.connect() as connection:
            # 目录只枚举渠道定位索引，逐渠道核对真实成员；系统身份不构成通配授权。
            for channel_id in await self.repository.directory(connection):
                member = await identity_one(
                    connection, "channel_memberships", channel_id, user_id=user_id
                )
                if member is None or member["status"] != "ACTIVE":
                    continue
                channel = await required(connection, "channels", channel_id, id=channel_id)
                if channel["status"] == "ARCHIVED":
                    continue
                environments = {
                    r["environment"]: r
                    for r in await rows(connection, "channel_environments", channel_id)
                }
                for domain in await rows(connection, "data_scopes", channel_id):
                    env = environments.get(domain["environment"])
                    if (
                        env is None
                        or env["environment"] not in member["environments"]
                        or domain["id"] not in member["data_scopes"]
                    ):
                        continue
                    result.append(
                        WorkspaceOption(
                            channel_id=channel_id,
                            channel_name=channel["name"],
                            environment=env["environment"],
                            environment_name=env["name"],
                            data_scope_id=domain["id"],
                            data_scope_name=domain["name"],
                        )
                    )
        return result


class ChannelResourceReader:
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
