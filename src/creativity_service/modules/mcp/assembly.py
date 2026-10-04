"""连接装配与服务端出站许可；不自动允许管理员输入的新目的地址。"""

from typing import Any

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy.ext.asyncio import AsyncEngine

from creativity_service.core.auth.types import ResourceState, ResourceStateReader
from creativity_service.core.context import AuthContext, Scope
from creativity_service.core.database import Repository
from creativity_service.core.database.tables import metadata as core_metadata
from creativity_service.core.security.credentials import CredentialService, KeyProvider
from creativity_service.core.security.outbound import Destination, OutboundPolicy
from creativity_service.integrations.tools.mcp_adapter import resolve_adapter
from creativity_service.modules.iam.authorization import IamAuthorization
from creativity_service.modules.mcp.repositories import repository
from creativity_service.modules.mcp.services import McpService
from creativity_service.modules.models.assembly import ConfiguredKeys
from creativity_service.modules.tools.assembly import ToolServices


class McpSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="CREATIVITY_MCP_", env_file=".env", extra="ignore", hide_input_in_errors=True
    )
    destinations: list[dict[str, Any]] = []
    key_version: str | None = None
    encryption_keys: dict[str, SecretStr] = {}


class McpResources:
    def __init__(self, engine: AsyncEngine, previous: ResourceStateReader | None) -> None:
        self.engine, self.previous = engine, previous

    async def read_current(
        self, context: AuthContext, resource_type: str, resource_id: str
    ) -> ResourceState | None:
        if resource_type in {"mcp_connection", "credential"}:
            async with self.engine.connect() as conn:
                repo = (
                    repository(context.scope, "mcp_connections")
                    if resource_type == "mcp_connection"
                    else Repository(core_metadata.tables["credentials"], context.scope)
                )
                row = await repo.get(conn, resource_id)
            if row and (resource_type != "credential" or row["purpose"] == "mcp"):
                return ResourceState(
                    scope=Scope(
                        channel_id=context.scope.channel_id, environment=context.scope.environment
                    ),
                    resource_type=resource_type,
                    resource_id=resource_id,
                    name=row.get("name", "MCP 服务凭据"),
                    active=resource_type == "mcp_connection" or row["state"] == "ACTIVE",
                )
        return (
            await self.previous.read_current(context, resource_type, resource_id)
            if self.previous
            else None
        )


class McpCredentialAuthorization:
    def __init__(self, authorization: IamAuthorization) -> None:
        self.authorization = authorization

    async def require(self, context: AuthContext, action: str, resource_id: str) -> None:
        if action == "credential:write" and resource_id == "mcp":
            await self.authorization.boundary(context, action, "channel", context.scope.channel_id)
        else:
            await self.authorization.boundary(context, action, "credential", resource_id)


def build_mcp_service(
    engine: AsyncEngine,
    authorization: IamAuthorization,
    tools: ToolServices,
    *,
    settings: McpSettings | None = None,
    key_provider: KeyProvider | None = None,
    outbound: OutboundPolicy | None = None,
) -> McpService:
    settings = settings or McpSettings()
    keys = key_provider or (
        ConfiguredKeys(settings.key_version, settings.encryption_keys)
        if settings.key_version
        else None
    )
    outbound = outbound or OutboundPolicy(tuple(Destination(**d) for d in settings.destinations))
    authorization.resources = McpResources(engine, authorization.resources)
    credentials = CredentialService(engine, keys, McpCredentialAuthorization(authorization))
    service = McpService(engine, authorization, tools.management, credentials, outbound)
    tools.registry.register_resolver(
        lambda scope, binding: resolve_adapter(service, scope, binding)
    )
    tools.management.binding_checks.append(service.check_binding)
    tools.management.binding_read_checks[service.check_binding] = service.read_bindings
    tools.management.binding_option_providers.append(service.bindings)
    return service
