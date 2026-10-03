"""授权码与 PKCE、一次性状态、分身份密文令牌及事务外刷新。"""

import base64
import hashlib
import secrets
from collections.abc import Awaitable, Callable
from datetime import timedelta
from typing import TYPE_CHECKING, Any, Literal
from urllib.parse import urlencode, urlsplit

from pydantic import Field, SecretBytes, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy import delete, select

from creativity_service.core.context import AuthContext, Scope
from creativity_service.core.database import Repository, transaction
from creativity_service.core.database.tables import metadata as core_metadata
from creativity_service.core.deletion import ContentRef, DeletionGuard, content_key
from creativity_service.core.locking import record_key
from creativity_service.core.observability.audit import append_audit
from creativity_service.core.primitives import (
    Contract,
    Identifier,
    ServiceError,
    canonical_json,
    digest,
    new_id,
    utcnow,
)
from creativity_service.core.security.credentials import CredentialService
from creativity_service.integrations.outbound import BoundedHttp
from creativity_service.modules.mcp.oauth_tables import metadata

if TYPE_CHECKING:
    from creativity_service.modules.mcp.services import McpService


class OAuthProfile(Contract):
    profile_id: Identifier
    name: str
    channels: frozenset[str]
    resource: str
    authorization_endpoint: str
    token_endpoint: str
    redirect_uri: str
    client_id: str
    client_secret: SecretStr | None = None
    scopes: tuple[str, ...] = ()


class OAuthSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="CREATIVITY_MCP_OAUTH_",
        env_file=".env",
        extra="ignore",
        hide_input_in_errors=True,
    )
    profiles: list[OAuthProfile] = []


class OAuthStart(Contract):
    profile_id: Identifier
    ownership: Literal["user", "service"] = "user"


class OAuthCallback(Contract):
    state: str = Field(min_length=32, max_length=256)
    code: SecretStr = Field(min_length=1, max_length=4096)


class OAuthView(Contract):
    grant_id: str
    name: str
    ownership: str
    status_label: str
    expires_at: str
    revision: int


class BoundKeys:
    def __init__(self, context: AuthContext, refs: set[str]) -> None:
        self.context, self.refs = context, refs

    async def require(self, context: AuthContext, action: str, resource_id: str) -> None:
        if context != self.context or not (
            (action == "credential:write" and resource_id == "mcp")
            or (action == "credential:use" and resource_id in self.refs)
        ):
            raise ServiceError("FORBIDDEN", "凭据不属于本次 OAuth 流程", 403)


class OAuthService:
    def __init__(
        self,
        service: "McpService",
        settings: OAuthSettings | None = None,
        http: BoundedHttp | None = None,
    ) -> None:
        self.service, self.settings = service, settings or OAuthSettings()
        self.http = http or BoundedHttp(service.outbound)

    def profile(self, context: AuthContext, identifier: str, endpoint: str) -> OAuthProfile:
        profile = next(
            (
                p
                for p in self.settings.profiles
                if p.profile_id == identifier
                and context.scope.channel_id in p.channels
                and p.resource == endpoint
            ),
            None,
        )
        if not profile or any(
            urlsplit(u).scheme not in {"https", "http"}
            or urlsplit(u).fragment
            or urlsplit(u).username
            for u in (
                profile.resource,
                profile.authorization_endpoint,
                profile.token_endpoint,
                profile.redirect_uri,
            )
        ):
            raise ServiceError("OAUTH_PROFILE_INVALID", "当前渠道没有匹配的身份提供方配置", 422)
        return profile

    @staticmethod
    def fingerprint(profile: OAuthProfile) -> str:
        return digest(
            [
                profile.model_dump(mode="json"),
                digest(profile.client_secret.get_secret_value()) if profile.client_secret else None,
            ]
        )

    @staticmethod
    def owner(context: AuthContext, ownership: str) -> str:
        return (
            digest(["service"])
            if ownership == "service"
            else digest(
                [
                    context.principal_id,
                    context.client_id,
                    context.scope.subject_type,
                    context.scope.subject_id,
                ]
            )
        )

    @staticmethod
    def scope_context(context: AuthContext, ownership: str) -> AuthContext:
        return (
            context.model_copy(
                update={
                    "scope": context.scope.model_copy(
                        update={"subject_type": None, "subject_id": None}
                    )
                }
            )
            if ownership == "service"
            else context
        )

    def credentials(self, context: AuthContext, *refs: str) -> CredentialService:
        return CredentialService(
            self.service.engine, self.service.credentials.keys, BoundKeys(context, set(refs))
        )

    async def require(self, context: AuthContext, connection_id: str, ownership: str) -> None:
        if ownership == "service" or context.principal_type == "management":
            await self.service.require(context, connection_id)
        else:
            await self.service.authorization.boundary(
                context, "run:create", "mcp_connection", connection_id
            )

    async def discard(self, context: AuthContext, reference: str) -> None:
        """流程结束、替换或撤销后清除不再使用的加密令牌。"""
        table = core_metadata.tables["credentials"]
        async with transaction(
            self.service.engine,
            context.scope,
            [
                content_key(context.scope),
                record_key(context.scope.channel_id, "credentials", reference),
            ],
        ) as uow:
            await uow.connection.execute(
                delete(table).where(
                    Repository(table, context.scope).predicate(), table.c.id == reference
                )
            )

    async def start(
        self, context: AuthContext, connection_id: str, body: OAuthStart
    ) -> dict[str, str]:
        await self.require(context, connection_id, body.ownership)
        row = await self.service.get(context, "mcp_connections", connection_id)
        if row["transport"] != "oauth":
            raise ServiceError("OAUTH_PROFILE_INVALID", "连接未使用 OAuth", 422)
        profile = self.profile(context, body.profile_id, row["endpoint"])
        await self.service.outbound.validate(context.scope, "oauth", profile.authorization_endpoint)
        await self.service.outbound.validate(context.scope, "oauth", profile.token_endpoint)
        state, verifier = secrets.token_urlsafe(32), secrets.token_urlsafe(48)
        identifier = digest(state)
        audit_id = new_id("audit")
        credential = await self.credentials(context).store(
            context, "mcp", SecretBytes(verifier.encode())
        )
        try:
            async with transaction(
                self.service.engine,
                context.scope,
                [
                    content_key(context.scope),
                    record_key(context.scope.channel_id, "mcp_oauth_flows", identifier),
                    record_key(context.scope.channel_id, "audit_events", audit_id),
                ],
            ) as uow:
                await DeletionGuard(context.scope).check(uow, [])
                await Repository(metadata.tables["mcp_oauth_flows"], context.scope).add(
                    uow,
                    identifier,
                    {
                        "connection_id": connection_id,
                        "configuration_revision": row["configuration_revision"],
                        "profile_id": profile.profile_id,
                        "profile_digest": self.fingerprint(profile),
                        "ownership": body.ownership,
                        "owner_id": self.owner(context, "user"),
                        "state": "PENDING",
                        "verifier_ref": credential,
                        "expires_at": utcnow() + timedelta(minutes=10),
                    },
                )
                await append_audit(
                    uow, context, audit_id, "mcp.oauth.start", "mcp_connection", connection_id
                )
        except BaseException:
            await self.discard(context, credential)
            raise
        challenge = (
            base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest())
            .decode()
            .rstrip("=")
        )
        params = urlencode(
            {
                "response_type": "code",
                "client_id": profile.client_id,
                "redirect_uri": profile.redirect_uri,
                "scope": " ".join(profile.scopes),
                "state": state,
                "code_challenge": challenge,
                "code_challenge_method": "S256",
                "resource": profile.resource,
            }
        )
        return {
            "authorization_url": profile.authorization_endpoint
            + ("&" if "?" in profile.authorization_endpoint else "?")
            + params,
            "expires_at": (utcnow() + timedelta(minutes=10)).isoformat(),
        }

    async def exchange(
        self, context: AuthContext, profile: OAuthProfile, params: dict[str, str]
    ) -> dict[str, Any]:
        params = {**params, "client_id": profile.client_id, "resource": profile.resource}
        if profile.client_secret:
            params["client_secret"] = profile.client_secret.get_secret_value()
        response = await self.http.post(
            context.scope,
            "oauth",
            profile.token_endpoint,
            urlencode(params).encode(),
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        if response.status != 200:
            raise ServiceError("OAUTH_EXCHANGE_FAILED", "身份提供方拒绝授权或刷新", 403)
        value = response.json()
        token, seconds = value.get("access_token"), value.get("expires_in")
        if (
            not isinstance(token, str)
            or not token
            or len(token) > 8192
            or any(ord(c) < 33 or ord(c) > 126 for c in token)
            or str(value.get("token_type", "")).lower() != "bearer"
            or type(seconds) is not int
            or not 1 <= seconds <= 86400
        ):
            raise ServiceError("OAUTH_RESPONSE_INVALID", "身份提供方令牌结构或有效期不正确", 502)
        if "scope" in value and (
            not isinstance(value["scope"], str)
            or not set(value["scope"].split()) <= set(profile.scopes)
        ):
            raise ServiceError("OAUTH_SCOPE_INVALID", "身份提供方返回了未申请权限", 502)
        refresh = value.get("refresh_token")
        if refresh is not None and (
            not isinstance(refresh, str) or not refresh or len(refresh) > 8192
        ):
            raise ServiceError("OAUTH_RESPONSE_INVALID", "刷新令牌格式不正确", 502)
        return {"access_token": token, "refresh_token": refresh, "expires_in": seconds}

    async def callback(self, context: AuthContext, body: OAuthCallback) -> OAuthView:
        identifier = digest(body.state)
        repo = Repository(metadata.tables["mcp_oauth_flows"], context.scope)
        async with transaction(
            self.service.engine,
            context.scope,
            [
                content_key(context.scope),
                record_key(context.scope.channel_id, "mcp_oauth_flows", identifier),
            ],
        ) as uow:
            await DeletionGuard(context.scope).check(uow, [ContentRef("oauth_flow", identifier)])
            flow = await repo.get(uow.connection, identifier)
            if (
                not flow
                or flow["state"] != "PENDING"
                or flow["expires_at"] <= utcnow()
                or flow["owner_id"] != self.owner(context, "user")
            ):
                raise ServiceError(
                    "OAUTH_STATE_INVALID", "授权状态已失效、已使用或不属于当前身份", 409
                )
            await repo.change(uow, identifier, flow["revision"], {"state": "EXCHANGING"})
        try:
            return await self.complete_callback(context, body, flow)
        finally:
            await self.discard(context, flow["verifier_ref"])

    async def complete_callback(
        self, context: AuthContext, body: OAuthCallback, flow: dict[str, Any]
    ) -> OAuthView:
        await self.require(context, flow["connection_id"], flow["ownership"])
        row = await self.service.get(context, "mcp_connections", flow["connection_id"])
        profile = self.profile(context, flow["profile_id"], row["endpoint"])
        if (
            row["configuration_revision"] != flow["configuration_revision"]
            or self.fingerprint(profile) != flow["profile_digest"]
        ):
            raise ServiceError("OAUTH_STATE_INVALID", "授权期间连接配置发生变化", 409)

        async def exchange(verifier: SecretBytes) -> dict[str, Any]:
            return await self.exchange(
                context,
                profile,
                {
                    "grant_type": "authorization_code",
                    "code": body.code.get_secret_value(),
                    "redirect_uri": profile.redirect_uri,
                    "code_verifier": verifier.get_secret_value().decode(),
                },
            )

        token = await self.credentials(context, flow["verifier_ref"]).call(
            context, flow["verifier_ref"], "mcp", exchange
        )
        owner = self.owner(context, flow["ownership"])
        scoped = self.scope_context(context, flow["ownership"])
        grant_id = digest([scoped.scope.model_dump(), row["id"], flow["ownership"], owner])
        ref = await self.credentials(scoped).store(
            scoped, "mcp", SecretBytes(canonical_json(token))
        )
        try:
            await self.require(context, flow["connection_id"], flow["ownership"])
            if (await self.service.get(context, "mcp_connections", row["id"]))[
                "configuration_revision"
            ] != flow["configuration_revision"]:
                raise ServiceError("OAUTH_STATE_INVALID", "授权期间连接配置发生变化", 409)
            grants = Repository(metadata.tables["mcp_oauth_tokens"], scoped.scope)
            audit_id = new_id("audit")
            async with transaction(
                self.service.engine,
                scoped.scope,
                [
                    content_key(scoped.scope),
                    record_key(scoped.scope.channel_id, "mcp_oauth_tokens", grant_id),
                    record_key(scoped.scope.channel_id, "mcp_connections", row["id"]),
                    record_key(scoped.scope.channel_id, "audit_events", audit_id),
                ],
            ) as uow:
                from creativity_service.modules.mcp.repositories import repository

                current = await repository(scoped.scope, "mcp_connections").get(
                    uow.connection, row["id"]
                )
                if (
                    not current
                    or current["configuration_revision"] != flow["configuration_revision"]
                ):
                    raise ServiceError("OAUTH_STATE_INVALID", "授权期间连接配置发生变化", 409)
                await DeletionGuard(scoped.scope).check(uow, [ContentRef("oauth_token", grant_id)])
                old = await grants.get(uow.connection, grant_id)
                if old and old["authorized_at"] > flow["created_at"]:
                    raise ServiceError("OAUTH_STATE_INVALID", "新的授权已经生效", 409)
                fields = {
                    "connection_id": row["id"],
                    "profile_id": profile.profile_id,
                    "profile_digest": flow["profile_digest"],
                    "ownership": flow["ownership"],
                    "owner_id": owner,
                    "credential_ref": ref,
                    "expires_at": utcnow() + timedelta(seconds=token["expires_in"]),
                    "state": "ACTIVE",
                    "refresh_until": None,
                    "refresh_nonce": None,
                    "authorized_at": flow["created_at"],
                }
                saved = (
                    await grants.change(uow, grant_id, old["revision"], fields)
                    if old
                    else await grants.add(uow, grant_id, fields)
                )
                await append_audit(
                    uow, scoped, audit_id, "mcp.oauth.authorize", "mcp_connection", row["id"]
                )
        except BaseException:
            await self.discard(scoped, ref)
            raise
        if old:
            await self.discard(scoped, old["credential_ref"])
        return self.view(saved, profile.name)

    @staticmethod
    def view(row: dict[str, Any], name: str) -> OAuthView:
        return OAuthView(
            grant_id=row["id"],
            name=name,
            ownership="服务" if row["ownership"] == "service" else "当前身份",
            status_label="已授权"
            if row["state"] == "ACTIVE"
            else "需重新授权"
            if row["state"] == "REAUTH_REQUIRED"
            else "已撤销",
            expires_at=row["expires_at"].isoformat(),
            revision=row["revision"],
        )

    async def grant(
        self, context: AuthContext, connection_id: str
    ) -> tuple[AuthContext, dict[str, Any]]:
        for ownership in ("user", "service"):
            scoped = self.scope_context(context, ownership)
            identifier = digest(
                [
                    scoped.scope.model_dump(),
                    connection_id,
                    ownership,
                    self.owner(context, ownership),
                ]
            )
            async with self.service.engine.connect() as connection:
                row = await Repository(metadata.tables["mcp_oauth_tokens"], scoped.scope).get(
                    connection, identifier
                )
            if row:
                if row["state"] != "ACTIVE":
                    raise ServiceError("OAUTH_REQUIRED", "委托已撤销，请重新授权", 403)
                async with transaction(
                    self.service.engine, scoped.scope, [content_key(scoped.scope)]
                ) as uow:
                    await DeletionGuard(scoped.scope).check(
                        uow, [ContentRef("oauth_token", identifier)]
                    )
                return scoped, row
        raise ServiceError("OAUTH_REQUIRED", "当前身份尚未完成 OAuth 授权", 403)

    async def call[T](
        self,
        context: AuthContext,
        connection: dict[str, Any],
        operation: Callable[[SecretBytes | None], Awaitable[T]],
    ) -> T:
        scoped, grant = await self.grant(context, connection["id"])
        profile = self.profile(context, grant["profile_id"], connection["endpoint"])
        if grant["profile_digest"] != self.fingerprint(profile):
            raise ServiceError("OAUTH_PROFILE_CHANGED", "身份提供方配置已变化，请重新授权", 403)
        if grant["expires_at"] <= utcnow() + timedelta(seconds=20):
            grant = await self.refresh(scoped, grant, profile)

        async def invoke(secret: SecretBytes) -> T:
            import json

            current_context, current = await self.grant(context, connection["id"])
            if current_context.scope != scoped.scope or current["revision"] != grant["revision"]:
                raise ServiceError("OAUTH_REVOKED", "委托授权已变化", 403)
            token = json.loads(secret.get_secret_value())
            return await operation(SecretBytes(token["access_token"].encode()))

        return await self.credentials(scoped, grant["credential_ref"]).call(
            scoped, grant["credential_ref"], "mcp", invoke
        )

    async def refresh(
        self, context: AuthContext, grant: dict[str, Any], profile: OAuthProfile
    ) -> dict[str, Any]:
        repo = Repository(metadata.tables["mcp_oauth_tokens"], context.scope)
        nonce = secrets.token_hex(16)
        keys = [
            content_key(context.scope),
            record_key(context.scope.channel_id, "mcp_oauth_tokens", grant["id"]),
        ]
        async with transaction(self.service.engine, context.scope, keys) as uow:
            await DeletionGuard(context.scope).check(uow, [ContentRef("oauth_token", grant["id"])])
            current = await repo.get(uow.connection, grant["id"])
            if not current or current["state"] != "ACTIVE":
                raise ServiceError("OAUTH_REVOKED", "委托已撤销", 403)
            if current["expires_at"] > utcnow() + timedelta(seconds=20):
                return current
            if current["refresh_until"] and current["refresh_until"] > utcnow():
                raise ServiceError("OAUTH_REFRESH_BUSY", "委托凭据正在刷新，请稍后重试", 503)
            if current["refresh_nonce"]:
                raise ServiceError("OAUTH_REQUIRED", "上次刷新结果未知，请重新授权", 403)
            current = await repo.change(
                uow,
                grant["id"],
                current["revision"],
                {"refresh_until": utcnow() + timedelta(seconds=60), "refresh_nonce": nonce},
            )

        async def exchange(secret: SecretBytes) -> dict[str, Any]:
            import json

            old = json.loads(secret.get_secret_value())
            if not old.get("refresh_token"):
                raise ServiceError("OAUTH_REQUIRED", "委托已到期，请重新授权", 403)
            token = await self.exchange(
                context,
                profile,
                {"grant_type": "refresh_token", "refresh_token": old["refresh_token"]},
            )
            token["refresh_token"] = token["refresh_token"] or old["refresh_token"]
            return token

        ref: str | None = None
        try:
            token = await self.credentials(context, current["credential_ref"]).call(
                context, current["credential_ref"], "mcp", exchange
            )
            ref = await self.credentials(context).store(
                context, "mcp", SecretBytes(canonical_json(token))
            )
            async with transaction(self.service.engine, context.scope, keys) as uow:
                latest = await repo.get(uow.connection, grant["id"])
                if not latest or latest["state"] != "ACTIVE" or latest["refresh_nonce"] != nonce:
                    raise ServiceError("OAUTH_REVOKED", "刷新期间委托已变化", 403)
                await DeletionGuard(context.scope).check(
                    uow, [ContentRef("oauth_token", grant["id"])]
                )
                saved = await repo.change(
                    uow,
                    grant["id"],
                    latest["revision"],
                    {
                        "credential_ref": ref,
                        "expires_at": utcnow() + timedelta(seconds=token["expires_in"]),
                        "refresh_until": None,
                        "refresh_nonce": None,
                    },
                )

        except BaseException:
            if ref:
                await self.discard(context, ref)
            async with transaction(self.service.engine, context.scope, keys) as uow:
                latest = await repo.get(uow.connection, grant["id"])
                if latest and latest["refresh_nonce"] == nonce:
                    await repo.change(
                        uow,
                        grant["id"],
                        latest["revision"],
                        {"state": "REAUTH_REQUIRED", "refresh_nonce": None, "refresh_until": None},
                    )
            await self.discard(context, current["credential_ref"])
            raise

        await self.discard(context, current["credential_ref"])
        return saved

    async def revoke(self, context: AuthContext, connection_id: str, ownership: str) -> None:
        await self.require(context, connection_id, ownership)
        scoped = self.scope_context(context, ownership)
        identifier = digest(
            [scoped.scope.model_dump(), connection_id, ownership, self.owner(context, ownership)]
        )
        repo = Repository(metadata.tables["mcp_oauth_tokens"], scoped.scope)
        audit_id = new_id("audit")
        async with transaction(
            self.service.engine,
            scoped.scope,
            [
                content_key(scoped.scope),
                record_key(scoped.scope.channel_id, "mcp_oauth_tokens", identifier),
                record_key(scoped.scope.channel_id, "audit_events", audit_id),
            ],
        ) as uow:
            row = await repo.get(uow.connection, identifier)
            if row:
                await repo.change(
                    uow,
                    identifier,
                    row["revision"],
                    {"state": "REVOKED", "refresh_nonce": None, "authorized_at": utcnow()},
                )
                await append_audit(
                    uow, scoped, audit_id, "mcp.oauth.revoke", "mcp_connection", connection_id
                )
        if row:
            await self.discard(scoped, row["credential_ref"])

    async def sweep(self, channel_id: str) -> None:
        for name, field in (
            ("mcp_oauth_flows", "expires_at"),
            ("mcp_oauth_tokens", "refresh_until"),
        ):
            table = metadata.tables[name]
            async with self.service.engine.connect() as connection:
                candidates = [
                    dict(r)
                    for r in (
                        await connection.execute(
                            select(table)
                            .where(
                                table.c.channel_id == channel_id,
                                table.c[field] <= utcnow(),
                                table.c.state.in_(
                                    {"PENDING", "EXCHANGING"}
                                    if name == "mcp_oauth_flows"
                                    else {"ACTIVE"}
                                ),
                            )
                            .limit(200)
                        )
                    ).mappings()
                ]
            for candidate in candidates:
                scope = Scope.model_validate({k: candidate[k] for k in Scope.model_fields})
                reference = candidate[
                    "verifier_ref" if name == "mcp_oauth_flows" else "credential_ref"
                ]
                async with transaction(
                    self.service.engine,
                    scope,
                    [
                        content_key(scope),
                        record_key(channel_id, name, candidate["id"]),
                        record_key(channel_id, "credentials", reference),
                    ],
                ) as uow:
                    repository = Repository(table, scope)
                    current = await repository.get(uow.connection, candidate["id"])
                    if not current or current["revision"] != candidate["revision"]:
                        continue
                    if name == "mcp_oauth_flows":
                        if current["state"] not in {"PENDING", "EXCHANGING"}:
                            continue
                        fields: dict[str, Any] = {"state": "EXPIRED"}
                    else:
                        if current["state"] != "ACTIVE" or not current["refresh_nonce"]:
                            continue
                        fields = {
                            "state": "REAUTH_REQUIRED",
                            "refresh_nonce": None,
                            "refresh_until": None,
                        }
                    await repository.change(uow, current["id"], current["revision"], fields)
                    credentials = core_metadata.tables["credentials"]
                    await uow.connection.execute(
                        delete(credentials).where(
                            Repository(credentials, scope).predicate(),
                            credentials.c.id == reference,
                        )
                    )
