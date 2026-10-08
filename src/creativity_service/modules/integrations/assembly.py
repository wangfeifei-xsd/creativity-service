"""独立委托密钥与 MCP 主体复核装配。"""

import base64
from dataclasses import dataclass

from pydantic import SecretBytes, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy.ext.asyncio import AsyncEngine

from creativity_service.core.primitives import unavailable
from creativity_service.core.security.credentials import KeyProvider
from creativity_service.modules.iam.authorization import IamAuthorization
from creativity_service.modules.integrations.delegation import (
    CurrentSubjectReader,
    DelegationService,
)
from creativity_service.modules.integrations.keys import DelegationKeys
from creativity_service.modules.integrations.subject_review import ConfiguredSubjectReader
from creativity_service.modules.mcp.services import McpService


class IntegrationSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="CREATIVITY_BUSINESS_",
        env_file=".env",
        extra="ignore",
        hide_input_in_errors=True,
    )
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


@dataclass(frozen=True)
class IntegrationServices:
    delegation: DelegationService
    keys: DelegationKeys
    subject_review: ConfiguredSubjectReader | None = None


def build_integration_services(
    engine: AsyncEngine,
    authorization: IamAuthorization,
    *,
    settings: IntegrationSettings | None = None,
    provider: KeyProvider | None = None,
    current_subjects: CurrentSubjectReader | None = None,
    mcp: McpService | None = None,
) -> IntegrationServices:
    settings = settings or IntegrationSettings()
    if provider is None and settings.key_version:
        provider = ConfiguredKeys(settings.key_version, settings.encryption_keys)
    keys = DelegationKeys(engine, authorization, provider)
    subject_review = ConfiguredSubjectReader(engine, authorization, mcp) if mcp else None
    delegation = DelegationService(keys, current_subjects or subject_review)
    authorization.subjects = delegation
    return IntegrationServices(delegation, keys, subject_review)
