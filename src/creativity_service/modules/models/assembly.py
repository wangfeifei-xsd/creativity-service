"""模型模块装配；出站白名单与密文主密钥仅从服务器配置读取。"""

import base64
from dataclasses import dataclass
from typing import Any

from pydantic import SecretBytes, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy.ext.asyncio import AsyncEngine

from creativity_service.core.auth.types import ResourceState, ResourceStateReader
from creativity_service.core.context import AuthContext, Scope
from creativity_service.core.database import transaction
from creativity_service.core.deletion import CleanupRegistry, ContentRef, DeletionGuard, content_key
from creativity_service.core.locking import record_key
from creativity_service.core.primitives import ServiceError
from creativity_service.core.security.credentials import CredentialService, KeyProvider
from creativity_service.core.security.outbound import Destination, OutboundPolicy
from creativity_service.integrations.models.adapter import LiteLLMAdapter
from creativity_service.modules.iam.services import IamServices
from creativity_service.modules.models.ports import DebugExecutor, ModelPriceReader
from creativity_service.modules.models.repositories import repository
from creativity_service.modules.models.routing import ModelRouting
from creativity_service.modules.models.services import ModelService
from creativity_service.modules.models.testing import ModelTesting


class ModelSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="CREATIVITY_MODEL_", env_file=".env", extra="ignore", hide_input_in_errors=True
    )
    destinations: list[dict[str, Any]] = []
    key_version: str | None = None
    encryption_keys: dict[str, SecretStr] = {}


class ConfiguredKeys:
    def __init__(self, current: str, values: dict[str, SecretStr]) -> None:
        self.version = current
        self.values = {
            k: SecretBytes(base64.b64decode(v.get_secret_value(), validate=True))
            for k, v in values.items()
        }
        if current not in self.values or any(
            len(v.get_secret_value()) != 32 for v in self.values.values()
        ):
            raise ValueError("模型凭据主密钥必须为三十二字节，并包含当前版本")

    async def current(self) -> tuple[str, SecretBytes]:
        return self.version, self.values[self.version]

    async def resolve(self, version: str) -> SecretBytes:
        if version not in self.values:
            raise ServiceError("CREDENTIAL_KEY_UNAVAILABLE", "模型凭据主密钥版本不可用", 503)
        return self.values[version]


class ModelCredentialAuthorization:
    def __init__(self, iam: IamServices) -> None:
        self.iam = iam

    async def require(self, context: AuthContext, action: str, resource_id: str) -> None:
        if action == "credential:write" and resource_id == "model":
            await self.iam.authorization.boundary(
                context, action, "channel", context.scope.channel_id
            )
        else:
            await self.iam.authorization.require(context, action, resource_id)


class ModelResourceReader:
    def __init__(self, engine: AsyncEngine, previous: ResourceStateReader | None) -> None:
        self.engine, self.previous = engine, previous

    async def read_current(
        self, context: AuthContext, resource_type: str, resource_id: str
    ) -> ResourceState | None:
        table = {
            "model": "models",
            "model_route": "model_routes",
            "model_connection": "model_connections",
            "credential": "credentials",
            "version": "resource_versions",
        }.get(resource_type)
        if table:
            async with self.engine.connect() as conn:
                row = await repository(context.scope, table).get(conn, resource_id)
                owned = (
                    row is not None
                    and (table != "credentials" or row["purpose"] == "model")
                    and (
                        table != "resource_versions"
                        or row["resource_type"] in {"model", "model_route", "model_connection"}
                    )
                )
                if owned and row:
                    connection_id = row.get("connection_id")
                    if table == "resource_versions":
                        if row["resource_type"] == "model_connection":
                            connection_id = row["resource_id"]
                        elif row["resource_type"] == "model_route" and any(
                            m["scope"]["environment"] != context.scope.environment
                            for m in row["content"].get("models", [])
                        ):
                            return None
                        elif row["resource_type"] == "model":
                            connection_id = row["content"].get("connection_id")
                    if (
                        connection_id
                        and await repository(context.scope, "model_connections").get(
                            conn, connection_id
                        )
                        is None
                    ):
                        return None
                    return ResourceState(
                        scope=Scope(
                            channel_id=context.scope.channel_id,
                            environment=context.scope.environment,
                        ),
                        resource_type=resource_type,
                        resource_id=resource_id,
                        name=row.get("name", row.get("version_label", "模型凭据")),
                        active=row.get("state") != "RETIRED"
                        if table == "resource_versions"
                        else row.get("status", row.get("state")) == "ACTIVE",
                    )
        return (
            await self.previous.read_current(context, resource_type, resource_id)
            if self.previous
            else None
        )


@dataclass(frozen=True)
class ModelServices:
    configuration: ModelService
    routing: ModelRouting
    testing: ModelTesting
    adapter: LiteLLMAdapter


def build_model_services(
    engine: AsyncEngine,
    iam: IamServices,
    *,
    settings: ModelSettings | None = None,
    key_provider: KeyProvider | None = None,
    outbound: OutboundPolicy | None = None,
    executor: DebugExecutor | None = None,
    prices: ModelPriceReader | None = None,
    cleanup: CleanupRegistry | None = None,
) -> ModelServices:
    settings = settings or ModelSettings()
    if key_provider is None and settings.key_version:
        key_provider = ConfiguredKeys(settings.key_version, settings.encryption_keys)
    outbound = outbound or OutboundPolicy(
        tuple(Destination(**value) for value in settings.destinations)
    )
    iam.authorization.resources = ModelResourceReader(engine, iam.authorization.resources)
    credentials = CredentialService(engine, key_provider, ModelCredentialAuthorization(iam))
    service = ModelService(engine, iam, credentials, outbound, executor, prices)
    routing = ModelRouting(service)
    if cleanup:

        async def clear(context: AuthContext, ref: ContentRef) -> None:
            await iam.authorization.require(context, "content:cleanup", "scope")
            async with transaction(
                engine,
                context.scope,
                [
                    content_key(context.scope),
                    record_key(context.scope.channel_id, "model_tests", ref.resource_id),
                ],
            ) as uow:
                try:
                    await DeletionGuard(context.scope).check(uow, [ref])
                except ServiceError as exc:
                    if exc.code != "CONTENT_DELETED":
                        raise
                else:
                    raise ServiceError("DELETION_MARKER_REQUIRED", "清理前必须登记删除标记")
                repo = repository(context.scope, "model_tests")
                row = await repo.get(uow.connection, ref.resource_id)
                if row:
                    await repo.change(
                        uow,
                        ref.resource_id,
                        row["revision"],
                        {"execution": {}, "cases": [], "results": [], "reason": None},
                    )

        cleanup.register("model_test", clear)
    return ModelServices(
        service,
        routing,
        ModelTesting(service),
        LiteLLMAdapter(credentials, outbound, routing.prepare_attempt),
    )
