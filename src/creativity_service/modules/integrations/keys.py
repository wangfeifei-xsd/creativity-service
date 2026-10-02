"""独立于渠道 API Key 的 HMAC 密钥生命周期与最小范围解密。"""

import base64
import os
from datetime import timedelta
from typing import Any

from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from sqlalchemy.ext.asyncio import AsyncEngine

from creativity_service.core.context import AuthContext
from creativity_service.core.database import Repository, assert_external_io_allowed, transaction
from creativity_service.core.database.tables import metadata as core_metadata
from creativity_service.core.locking import record_key
from creativity_service.core.observability.audit import append_audit
from creativity_service.core.primitives import ServiceError, new_id, unavailable, utcnow
from creativity_service.core.security.credentials import CredentialService, KeyProvider
from creativity_service.modules.channels.repositories import required
from creativity_service.modules.iam.authorization import IamAuthorization
from creativity_service.modules.iam.repositories import policy_key
from creativity_service.modules.integrations.authorization import require_management
from creativity_service.modules.integrations.repositories import (
    configuration_key,
    environment_scope,
    repository,
)
from creativity_service.modules.integrations.schemas import (
    DelegationKeyCreate,
    DelegationKeyIssued,
    DelegationKeyRotate,
    DelegationKeyView,
)


class DelegationKeys:
    def __init__(
        self, engine: AsyncEngine, authorization: IamAuthorization, provider: KeyProvider | None
    ) -> None:
        self.engine, self.authorization, self.provider = engine, authorization, provider

    async def require(self, context: AuthContext) -> None:
        if context.principal_type != "management":
            raise ServiceError("FORBIDDEN", "此操作需要管理身份", 403)
        await self.authorization.boundary(
            context, "key:manage", "channel", context.scope.channel_id
        )

    async def view(self, context: AuthContext, row: dict[str, Any]) -> DelegationKeyView:
        async with self.engine.connect() as connection:
            client = await required(
                connection,
                "service_clients",
                context.scope.channel_id,
                id=row["client_id"],
                environment=context.scope.environment,
            )
            successors = await repository(environment_scope(context.scope), "delegation_keys").find(
                connection, rotated_from=row["id"]
            )
        names = {"ACTIVE": "启用", "REVOKED": "已吊销"}
        return DelegationKeyView(
            **{k: row[k] for k in DelegationKeyView.model_fields if k in row},
            kid=row["id"],
            client_name=client["name"],
            status_name=(
                "已吊销"
                if row["status"] == "REVOKED"
                else "已到期"
                if row["expires_at"] <= utcnow()
                else "轮换重叠期"
                if successors
                else names[row["status"]]
            ),
        )

    async def list(self, context: AuthContext) -> list[DelegationKeyView]:
        await self.require(context)
        scope = environment_scope(context.scope)
        member = await self.authorization.authentication.active_member(context)
        async with self.engine.connect() as connection:
            values = await repository(scope, "delegation_keys").find(connection)
            visible = []
            for row in values:
                client = await required(
                    connection,
                    "service_clients",
                    scope.channel_id,
                    id=row["client_id"],
                    environment=scope.environment,
                )
                if set(client["data_scopes"]) <= set(member.data_scopes):
                    visible.append(row)
        return [await self.view(context, row) for row in visible]

    async def create(
        self,
        context: AuthContext,
        body: DelegationKeyCreate,
        *,
        previous_id: str | None = None,
        rotation: DelegationKeyRotate | None = None,
    ) -> DelegationKeyIssued:
        await self.require(context)
        if body.expires_at <= utcnow() + timedelta(seconds=body.max_ttl_seconds):
            raise ServiceError("VALIDATION_ERROR", "密钥到期时间须覆盖委托有效期", 422)
        if self.provider is None:
            raise unavailable("业务接入凭据主密钥")
        assert_external_io_allowed()
        version, master = await self.provider.current()
        kid, credential_id, audit_id = new_id("dk"), new_id("credential"), new_id("audit")
        secret, nonce = os.urandom(32), os.urandom(12)
        encrypted = nonce + AESGCM(master.get_secret_value()).encrypt(
            nonce, secret, CredentialService.aad(context, credential_id, "delegation")
        )
        scope = environment_scope(context.scope)
        locks = [
            configuration_key(scope),
            policy_key(scope.channel_id),
            policy_key("system"),
            record_key(scope.channel_id, "delegation_keys", kid),
            record_key(scope.channel_id, "credentials", credential_id),
            record_key(scope.channel_id, "audit_events", audit_id),
        ]
        if previous_id:
            locks.append(record_key(scope.channel_id, "delegation_keys", previous_id))
        async with transaction(self.engine, scope, locks) as uow:
            client = await required(
                uow.connection,
                "service_clients",
                scope.channel_id,
                id=body.client_id,
                environment=scope.environment,
            )
            await require_management(uow, context, "key:manage", client["data_scopes"])
            if client["status"] != "ACTIVE":
                raise ServiceError("CLIENT_REVOKED", "接入服务不可用", 403)
            repo = repository(scope, "delegation_keys")
            if previous_id and rotation:
                previous = await repo.get(uow.connection, previous_id)
                if (
                    previous is None
                    or previous["client_id"] != body.client_id
                    or previous["status"] != "ACTIVE"
                    or previous["expires_at"] <= utcnow()
                ):
                    raise ServiceError("DELEGATION_KEY_UNAVAILABLE", "委托密钥不可轮换", 409)
                if await repo.find(uow.connection, rotated_from=previous_id):
                    raise ServiceError("DELEGATION_KEY_ROTATED", "旧密钥已轮换，请使用新密钥", 409)
                await repo.change(
                    uow,
                    previous_id,
                    rotation.revision,
                    {
                        "expires_at": min(
                            previous["expires_at"],
                            utcnow() + timedelta(seconds=rotation.overlap_seconds),
                        )
                    },
                )
            elif any(
                r["status"] == "ACTIVE" and r["expires_at"] > utcnow()
                for r in await repo.find(uow.connection, client_id=body.client_id)
            ):
                raise ServiceError("DELEGATION_KEY_EXISTS", "该接入服务已有密钥，请使用轮换", 409)
            await Repository(core_metadata.tables["credentials"], scope).add(
                uow,
                credential_id,
                {
                    "purpose": "delegation",
                    "ciphertext": encrypted,
                    "key_version": version,
                    "state": "ACTIVE",
                },
            )
            row = await repo.add(
                uow,
                kid,
                {
                    **body.model_dump(),
                    "key_reference": credential_id,
                    "algorithm": "HMAC-SHA256",
                    "status": "ACTIVE",
                    "not_before": utcnow(),
                    "rotated_from": previous_id,
                },
            )
            await append_audit(
                uow,
                context.model_copy(update={"scope": scope}),
                audit_id,
                "delegation_key.rotate" if previous_id else "delegation_key.create",
                "delegation_key",
                kid,
                {"previous_version_id": previous_id},
            )
        return DelegationKeyIssued(
            key=await self.view(context, row),
            signing_secret=base64.b64encode(secret).decode("ascii"),
        )

    async def rotate(
        self, context: AuthContext, kid: str, body: DelegationKeyRotate
    ) -> DelegationKeyIssued:
        await self.require(context)
        async with self.engine.connect() as connection:
            row = await repository(environment_scope(context.scope), "delegation_keys").get(
                connection, kid
            )
        if row is None:
            raise ServiceError("NOT_FOUND", "委托密钥不存在", 404)
        create = DelegationKeyCreate(**{k: row[k] for k in DelegationKeyCreate.model_fields})
        return await self.create(
            context,
            create.model_copy(update={"expires_at": body.expires_at}),
            previous_id=kid,
            rotation=body,
        )

    async def revoke(self, context: AuthContext, kid: str, revision: int) -> DelegationKeyView:
        await self.require(context)
        scope, audit_id = environment_scope(context.scope), new_id("audit")
        async with transaction(
            self.engine,
            scope,
            [
                configuration_key(scope),
                policy_key(scope.channel_id),
                policy_key("system"),
                record_key(scope.channel_id, "delegation_keys", kid),
                record_key(scope.channel_id, "audit_events", audit_id),
            ],
        ) as uow:
            repo = repository(scope, "delegation_keys")
            row = await repo.get(uow.connection, kid)
            if row is None:
                raise ServiceError("NOT_FOUND", "委托密钥不存在", 404)
            client = await required(
                uow.connection,
                "service_clients",
                scope.channel_id,
                id=row["client_id"],
                environment=scope.environment,
            )
            await require_management(uow, context, "key:manage", client["data_scopes"])
            row = await repo.change(uow, kid, revision, {"status": "REVOKED"})
            await append_audit(
                uow,
                context.model_copy(update={"scope": scope}),
                audit_id,
                "delegation_key.revoke",
                "delegation_key",
                kid,
                {"state": "REVOKED"},
            )
        return await self.view(context, row)

    async def secret(self, context: AuthContext, row: dict[str, Any]) -> bytes:
        # 仅供已按 Token 渠道、环境、client_id 和 kid 定位的验签入口使用。
        assert_external_io_allowed()
        if self.provider is None:
            raise unavailable("业务接入凭据主密钥")
        if (row["channel_id"], row["environment"], row["client_id"]) != (
            context.scope.channel_id,
            context.scope.environment,
            context.client_id,
        ):
            raise ServiceError("DELEGATION_INVALID", "业务主体委托无效", 401)
        scope = environment_scope(context.scope)
        async with self.engine.connect() as connection:
            credential = await Repository(core_metadata.tables["credentials"], scope).get(
                connection, row["key_reference"]
            )
        if (
            not credential
            or credential["purpose"] != "delegation"
            or credential["state"] != "ACTIVE"
        ):
            raise unavailable("委托验签凭据")
        master = await self.provider.resolve(credential["key_version"])
        try:
            data = bytes(credential["ciphertext"])
            secret = AESGCM(master.get_secret_value()).decrypt(
                data[:12], data[12:], CredentialService.aad(context, credential["id"], "delegation")
            )
            if len(secret) < 32:
                raise ValueError
            return secret
        except Exception as exc:
            raise ServiceError("CREDENTIAL_DECRYPT_FAILED", "委托凭据无法解密", 503) from exc
