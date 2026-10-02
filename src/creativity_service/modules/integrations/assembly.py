"""业务接入装配；未配置主密钥或目的地时保持关闭。"""

import base64
import os
from dataclasses import dataclass
from typing import Any, Literal, cast

from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from pydantic import SecretBytes, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy.ext.asyncio import AsyncEngine

from creativity_service.core.context import AuthContext
from creativity_service.core.database import Repository, assert_external_io_allowed, transaction
from creativity_service.core.database.tables import metadata as core_metadata
from creativity_service.core.locking import record_key
from creativity_service.core.observability.audit import append_audit
from creativity_service.core.primitives import ServiceError, new_id, unavailable
from creativity_service.core.security.credentials import CredentialService, KeyProvider
from creativity_service.core.security.outbound import Destination, OutboundPolicy
from creativity_service.integrations.business.base import (
    OPERATION_NAMES,
    BusinessCall,
    Capability,
    Operation,
)
from creativity_service.integrations.business.base.http import StandardHttpAdapter
from creativity_service.integrations.business.registry import BusinessRegistry, Registration
from creativity_service.modules.iam.authorization import IamAuthorization
from creativity_service.modules.iam.repositories import policy_key
from creativity_service.modules.integrations.authorization import require_management
from creativity_service.modules.integrations.delegation import (
    CurrentSubjectReader,
    DelegationService,
)
from creativity_service.modules.integrations.keys import DelegationKeys
from creativity_service.modules.integrations.repositories import configuration_key, repository
from creativity_service.modules.integrations.services import IntegrationService


class IntegrationSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="CREATIVITY_BUSINESS_",
        env_file=".env",
        extra="ignore",
        hide_input_in_errors=True,
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
            raise ValueError("业务凭据主密钥必须为三十二字节并包含当前版本")

    async def current(self) -> tuple[str, SecretBytes]:
        return self.version, self.values[self.version]

    async def resolve(self, version: str) -> SecretBytes:
        if version not in self.values:
            raise unavailable("业务凭据主密钥版本")
        return self.values[version]


class CredentialAuthorization:
    def __init__(self, authorization: IamAuthorization) -> None:
        self.authorization = authorization

    async def require(self, context: AuthContext, action: str, resource_id: str) -> None:
        if (
            context.principal_type != "management"
            or action != "credential:write"
            or resource_id != "http_tool"
        ):
            raise ServiceError("FORBIDDEN", "凭据操作未获授权", 403)
        for required in ("integration:manage", "credential:write"):
            await self.authorization.boundary(
                context, required, "channel", context.scope.channel_id
            )


class IntegrationCredentialService(CredentialService):
    async def store(
        self,
        context: AuthContext,
        purpose: Literal["model", "mcp", "http_tool", "delegation"],
        plaintext: SecretBytes,
    ) -> str:
        assert_external_io_allowed()
        value = plaintext.get_secret_value()
        if (
            purpose != "http_tool"
            or not 1 <= len(value) <= 4096
            or any(c < 33 or c > 126 for c in value)
        ):
            raise ServiceError("VALIDATION_ERROR", "业务服务凭据格式不正确", 422)
        await self.authorization.require(context, "credential:write", purpose)
        if self.keys is None:
            raise unavailable("业务连接凭据主密钥")
        version, master = await self.keys.current()
        credential_id, audit_id, nonce = new_id("credential"), new_id("audit"), os.urandom(12)
        encrypted = nonce + AESGCM(master.get_secret_value()).encrypt(
            nonce, value, self.aad(context, credential_id, purpose)
        )
        scope = context.scope
        async with transaction(
            self.engine,
            scope,
            [
                configuration_key(scope),
                policy_key(scope.channel_id),
                policy_key("system"),
                record_key(scope.channel_id, "credentials", credential_id),
                record_key(scope.channel_id, "audit_events", audit_id),
            ],
        ) as uow:
            for action in ("integration:manage", "credential:write"):
                await require_management(uow, context, action)
            await Repository(core_metadata.tables["credentials"], scope).add(
                uow,
                credential_id,
                {
                    "purpose": purpose,
                    "ciphertext": encrypted,
                    "key_version": version,
                    "state": "ACTIVE",
                },
            )
            await append_audit(
                uow,
                context,
                audit_id,
                "integration_credential.create",
                "credential",
                credential_id,
                {"state": "ACTIVE"},
            )
        return credential_id


class ConnectionCredentials:
    def __init__(
        self,
        engine: AsyncEngine,
        provider: KeyProvider | None,
        authorization: IamAuthorization,
        delegation: DelegationService,
    ) -> None:
        self.engine, self.provider, self.authorization, self.delegation = (
            engine,
            provider,
            authorization,
            delegation,
        )

    async def headers(self, call: BusinessCall) -> str:
        assert_external_io_allowed()
        if self.provider is None:
            raise unavailable("业务连接凭据主密钥")
        context, configured = call.context, call.connection
        async with self.engine.connect() as connection:
            row = await repository(context.scope, "integrations").get(connection, configured["id"])
            credential = await Repository(core_metadata.tables["credentials"], context.scope).get(
                connection, configured["credential_ref"]
            )
        if (
            not row
            or row["revision"] != configured["revision"]
            or row["status"] != "ACTIVE"
            or row["credential_ref"] != configured["credential_ref"]
            or call.operation not in row["allowed_operations"]
            or not credential
            or credential["purpose"] != "http_tool"
            or credential["state"] != "ACTIVE"
        ):
            raise ServiceError("CREDENTIAL_UNAVAILABLE", "业务连接凭据不可用", 403)
        key = await self.provider.resolve(credential["key_version"])
        try:
            value = bytes(credential["ciphertext"])
            plaintext = (
                AESGCM(key.get_secret_value())
                .decrypt(
                    value[:12],
                    value[12:],
                    CredentialService.aad(context, credential["id"], "http_tool"),
                )
                .decode("utf-8")
            )
            if not plaintext or any(ord(c) < 33 or ord(c) > 126 for c in plaintext):
                raise ValueError
        except Exception as exc:
            raise ServiceError("CREDENTIAL_DECRYPT_FAILED", "业务凭据无法读取", 503) from exc
        await self.authorization.authentication.revalidate(context)
        if not context.actor_id:
            await self.delegation.read_current(context)
        async with self.engine.connect() as connection:
            current = await repository(context.scope, "integrations").get(
                connection, configured["id"]
            )
            active = await Repository(core_metadata.tables["credentials"], context.scope).get(
                connection, credential["id"]
            )
        if current != row or active != credential:
            raise ServiceError("CREDENTIAL_UNAVAILABLE", "业务连接凭据已变化", 403)
        return "Bearer " + plaintext


@dataclass(frozen=True)
class IntegrationServices:
    management: IntegrationService
    delegation: DelegationService
    keys: DelegationKeys
    credentials: CredentialService
    registry: BusinessRegistry


def build_integration_services(
    engine: AsyncEngine,
    authorization: IamAuthorization,
    *,
    settings: IntegrationSettings | None = None,
    provider: KeyProvider | None = None,
    outbound: OutboundPolicy | None = None,
    registry: BusinessRegistry | None = None,
    current_subjects: CurrentSubjectReader | None = None,
) -> IntegrationServices:
    settings = settings or IntegrationSettings()
    if provider is None and settings.key_version:
        provider = ConfiguredKeys(settings.key_version, settings.encryption_keys)
    outbound = outbound or OutboundPolicy(tuple(Destination(**v) for v in settings.destinations))
    keys = DelegationKeys(engine, authorization, provider)
    delegation = DelegationService(keys, current_subjects)
    registry = registry or BusinessRegistry()
    transport = StandardHttpAdapter(
        outbound, ConnectionCredentials(engine, provider, authorization, delegation).headers
    )
    registry.register(
        Registration(
            "standard_http",
            "标准业务接口",
            "1.0.0",
            transport,
            tuple(
                Capability(
                    operation=cast(Operation, op),
                    name=name,
                    public=op in {"dictionary", "candidates", "quote", "recheck"},
                    required_actions=["metric:read" if op.startswith("metric_") else "run:create"],
                )
                for op, name in OPERATION_NAMES.items()
            ),
        )
    )
    authorization.subjects = delegation
    return IntegrationServices(
        IntegrationService(engine, authorization, registry, outbound, delegation),
        delegation,
        keys,
        IntegrationCredentialService(engine, provider, CredentialAuthorization(authorization)),
        registry,
    )
