"""仅信任服务端登记的身份校验端点，映射既有成员后签发本平台随机令牌。"""

from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any
from urllib.parse import urlencode

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

from creativity_service.core.auth.types import TokenResponse
from creativity_service.core.context import Scope
from creativity_service.core.database import transaction
from creativity_service.core.locking import record_key
from creativity_service.core.primitives import (
    Contract,
    Identifier,
    ServiceError,
    digest,
    new_id,
    utcnow,
)
from creativity_service.core.security.outbound import Destination, OutboundPolicy
from creativity_service.integrations.outbound import BoundedHttp
from creativity_service.modules.iam.audit import append_event
from creativity_service.modules.iam.repositories import one, policy_key

if TYPE_CHECKING:
    from creativity_service.modules.iam.services import IamServices


class IdentityProfile(Contract):
    profile_id: Identifier
    name: str
    scope: Scope
    issuer: str
    audience: str
    endpoint: str
    client_id: str
    client_secret: SecretStr
    subjects: dict[str, Identifier]


class IdentitySettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="CREATIVITY_IDENTITY_",
        env_file=".env",
        extra="ignore",
        hide_input_in_errors=True,
    )
    profiles: list[IdentityProfile] = []
    destinations: list[dict[str, Any]] = []


class IdentityExchange(Contract):
    profile_id: Identifier
    token: SecretStr = Field(min_length=1, max_length=8192)


class ExternalIdentity:
    def __init__(
        self,
        iam: "IamServices",
        settings: IdentitySettings | None = None,
        http: BoundedHttp | None = None,
    ) -> None:
        self.iam, self.settings = iam, settings or IdentitySettings()
        self.http = http or BoundedHttp(
            OutboundPolicy(tuple(Destination(**v) for v in self.settings.destinations))
        )

    def profiles(self) -> list[dict[str, str]]:
        return [{"profile_id": p.profile_id, "name": p.name} for p in self.settings.profiles]

    async def exchange(
        self, body: IdentityExchange, remote_ip: str, request_id: str
    ) -> TokenResponse:
        profile = next((p for p in self.settings.profiles if p.profile_id == body.profile_id), None)
        if not profile or profile.scope.subject_id:
            raise ServiceError("IDENTITY_SOURCE_UNAVAILABLE", "身份源未配置或不可用", 403)
        await self.iam.authentication.tokens.limit_login(
            digest([profile.profile_id, body.token.get_secret_value()]), remote_ip
        )
        response = await self.http.post(
            profile.scope,
            "identity",
            profile.endpoint,
            urlencode(
                {
                    "token": body.token.get_secret_value(),
                    "token_type_hint": "access_token",
                    "client_id": profile.client_id,
                    "client_secret": profile.client_secret.get_secret_value(),
                }
            ).encode(),
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        if response.status != 200:
            raise ServiceError("IDENTITY_SOURCE_UNAVAILABLE", "身份源校验失败", 503)
        claims = response.json()
        audience, expires, subject = claims.get("aud"), claims.get("exp"), claims.get("sub")
        if (
            claims.get("active") is not True
            or claims.get("iss") != profile.issuer
            or not (
                audience == profile.audience
                or isinstance(audience, list)
                and profile.audience in audience
            )
            or not isinstance(subject, str)
            or not subject
            or type(expires) is not int
            or not int(utcnow().timestamp()) < expires <= 253402300799
            or (
                "nbf" in claims
                and (type(claims["nbf"]) is not int or claims["nbf"] > utcnow().timestamp())
            )
        ):
            raise ServiceError("EXTERNAL_IDENTITY_INVALID", "外部身份无效、受众不符或已过期", 401)
        account_id = profile.subjects.get(subject)
        if not account_id:
            raise ServiceError("IDENTITY_MAPPING_MISSING", "外部身份尚未关联平台账号", 403)
        event_id = new_id("audit")
        scope = profile.scope
        async with transaction(
            self.iam.accounts.repository.engine,
            scope,
            [
                policy_key("system"),
                policy_key(scope.channel_id),
                record_key(scope.channel_id, "audit_events", event_id),
            ],
        ) as uow:
            account = await one(uow.connection, "platform_accounts", "system", id=account_id)
            member = await one(
                uow.connection, "channel_memberships", scope.channel_id, user_id=account_id
            )
            if not account or account["status"] != "ACTIVE" or account["must_change_password"]:
                raise ServiceError("ACCOUNT_DISABLED", "平台账号当前不可用", 401)
            if (
                not member
                or member["status"] != "ACTIVE"
                or scope.environment not in member["environments"]
            ):
                raise ServiceError("MEMBERSHIP_DISABLED", "平台成员未获目标渠道环境授权", 403)
            await append_event(
                uow, event_id, account_id, request_id, "auth:external", "account", account_id
            )
        expiry = datetime.fromtimestamp(expires, UTC)
        result, record = await self.iam.authentication.tokens.issue(
            purpose="management",
            principal_id=account_id,
            channel_id=scope.channel_id,
            environment=scope.environment,
            credential_version=account["credential_version"],
            membership_version=member["revision"],
            expires_by=expiry,
            upstream_expires_at=expiry,
            identity_channel_id=scope.channel_id,
        )
        await self.iam.authentication.validate_record(record)
        return result
