"""随机接入 Key、同事务身份索引、轮换与服务 Token 交换。"""

import hashlib
import hmac
import secrets
from datetime import timedelta
from typing import Any

from sqlalchemy.ext.asyncio import AsyncConnection

from creativity_service.core.auth.authentication import AdminSession
from creativity_service.core.auth.types import TokenResponse
from creativity_service.core.context import AuthContext, ControlScope, Scope
from creativity_service.core.database import UnitOfWork, transaction
from creativity_service.core.locking import ResourceKey, record_key
from creativity_service.core.primitives import ServiceError, new_id, utcnow
from creativity_service.modules.channels.reading import ChannelReadData
from creativity_service.modules.channels.repositories import one, required, rows, save
from creativity_service.modules.channels.schemas import (
    KeyCreate,
    KeyCreated,
    KeyRotate,
    KeyView,
    TokenExchange,
)
from creativity_service.modules.channels.services import ChannelService, capabilities, clean_name
from creativity_service.modules.channels.state import current_service, require_available
from creativity_service.modules.iam.audit import append_event
from creativity_service.modules.iam.revocations import enqueue
from creativity_service.modules.iam.roles import ACTION_NAMES


def secret_digest(secret: str) -> str:
    return hashlib.sha256(secret.encode()).hexdigest()


class KeyService:
    def __init__(self, channels: ChannelService) -> None:
        self.channels, self.repository, self.iam = channels, channels.repository, channels.iam

    async def view(
        self, connection: AsyncConnection, row: dict[str, Any], data: ChannelReadData | None = None
    ) -> KeyView:
        client = (
            data.clients.get(row["client_id"])
            if data is not None
            else await one(connection, "service_clients", row["channel_id"], id=row["client_id"])
        )
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
        status = (
            "EXPIRED"
            if row["status"] == "ACTIVE" and row["expires_at"] <= utcnow()
            else row["status"]
        )
        return KeyView(
            key_id=row["id"],
            client_name=client["name"] if client else None,
            environment_name=env["name"] if env else None,
            masked_key=f"{row['prefix']}…{row['suffix']}",
            scope_names=[ACTION_NAMES[a] for a in row["scopes"]],
            status=status,
            status_label={"ACTIVE": "启用", "EXPIRED": "已到期", "REVOKED": "已吊销"}[status],
            **{
                k: row[k]
                for k in (
                    "name",
                    "client_id",
                    "environment",
                    "scopes",
                    "expires_at",
                    "last_used_at",
                    "created_at",
                    "revision",
                )
            },
        )

    async def list_items(self, session: AdminSession, channel_id: str) -> list[KeyView]:
        await self.channels.authorize(session, channel_id, "key:manage")
        async with self.repository.engine.connect() as connection:
            await required(connection, "channels", channel_id, id=channel_id)
            data = await ChannelReadData.load(connection, session, channel_id)
            result = []
            for row in await rows(connection, "channel_keys", channel_id):
                client = data.clients.get(row["client_id"])
                if client is None:
                    raise ServiceError("NOT_FOUND", "接入服务不存在", 404)
                if data.visible(row["environment"], action="key:manage"):
                    result.append(await self.view(connection, row, data))
            return result

    def creation_keys(
        self, channel_id: str, key_id: str, event_id: str, digest: str
    ) -> list[ResourceKey]:
        return self.channels.keys(channel_id, "channel_keys", key_id, event_id) + [
            record_key("system", "key_identity_index", key_id),
            ResourceKey("system", "key_identity_index", (digest,)),
        ]

    async def add(
        self, uow: UnitOfWork, session: AdminSession, key_id: str, secret: str, body: KeyCreate
    ) -> dict[str, Any]:
        channel_id = uow.scope.channel_id
        channel = await self.channels.locked(uow, session, "key:manage")
        client = await required(
            uow.connection,
            "service_clients",
            channel_id,
            id=body.client_id,
            environment=body.environment,
        )
        env = await required(
            uow.connection, "channel_environments", channel_id, environment=body.environment
        )
        require_available(channel, env)
        if client["status"] != "ACTIVE":
            raise ServiceError("CLIENT_DISABLED", "接入服务已停用", 409)
        if body.expires_at <= utcnow():
            raise ServiceError("INVALID_EXPIRY", "接入 Key 有效期须晚于当前时间", 422)
        capabilities(body.scopes)
        if not set(body.scopes) <= set(client["scopes"]):
            raise ServiceError("KEY_SCOPE_EXCEEDED", "Key 权限不能超过接入服务授权", 403)
        await self.channels.require_visible(
            uow.connection,
            session,
            body.environment,
            action="key:manage",
            delegated=body.scopes,
        )
        row = await save(
            uow,
            "channel_keys",
            key_id,
            {
                **body.model_dump(),
                "name": clean_name(body.name),
                "secret_digest": secret_digest(secret),
                "prefix": secret[:10],
                "suffix": secret[-4:],
                "status": "ACTIVE",
                "last_used_at": None,
            },
        )
        await self.repository.add_index(
            uow,
            "key_identity_index",
            key_id,
            {
                "key_lookup_digest": secret_digest(secret),
                "key_id": key_id,
                "target_channel_id": channel_id,
            },
        )
        return row

    async def create(self, session: AdminSession, channel_id: str, body: KeyCreate) -> KeyCreated:
        await self.channels.authorize(session, channel_id, "key:manage", credential=True)
        key_id, event_id, secret = (
            new_id("key"),
            new_id("audit"),
            "chn_" + secrets.token_urlsafe(32),
        )
        async with transaction(
            self.repository.engine,
            self.channels.scope(session, channel_id),
            self.creation_keys(channel_id, key_id, event_id, secret_digest(secret)),
        ) as uow:
            row = await self.add(uow, session, key_id, secret, body)
            await self.channels.event(uow, event_id, session, "key:create", "key", row)
            view = await self.view(uow.connection, row)
        return KeyCreated(key=view, api_key=secret)

    async def rotate(
        self, session: AdminSession, channel_id: str, old_key_id: str, body: KeyRotate
    ) -> KeyCreated:
        await self.channels.authorize(session, channel_id, "key:manage", credential=True)
        key_id, event_id, rotation_id = new_id("key"), new_id("audit"), new_id("rotation")
        secret, revoke_id = "chn_" + secrets.token_urlsafe(32), new_id("revoke")
        keys = self.creation_keys(channel_id, key_id, event_id, secret_digest(secret)) + [
            record_key(channel_id, "channel_keys", old_key_id),
            record_key(channel_id, "key_rotations", rotation_id),
            record_key(channel_id, "iam_revocations", revoke_id),
        ]
        revocation = None
        async with transaction(
            self.repository.engine, self.channels.scope(session, channel_id), keys
        ) as uow:
            await self.channels.locked(uow, session, "key:manage")
            old = await required(uow.connection, "channel_keys", channel_id, id=old_key_id)
            if (
                old["status"] != "ACTIVE"
                or old["expires_at"] <= utcnow()
                or await one(uow.connection, "key_rotations", channel_id, old_key_id=old_key_id)
            ):
                raise ServiceError(
                    "INVALID_STATE", "此 Key 不可再次轮换，请使用新 Key 或创建凭据", 409
                )
            client = await required(
                uow.connection, "service_clients", channel_id, id=old["client_id"]
            )
            scopes = [a for a in old["scopes"] if a in client["scopes"]]
            row = await self.add(
                uow,
                session,
                key_id,
                secret,
                KeyCreate(
                    name=old["name"],
                    client_id=old["client_id"],
                    environment=old["environment"],
                    scopes=scopes,
                    expires_at=body.expires_at,
                ),
            )
            overlap_until = min(
                old["expires_at"], utcnow() + timedelta(seconds=body.overlap_seconds)
            )
            await save(
                uow,
                "channel_keys",
                old_key_id,
                {
                    "expires_at": overlap_until,
                    "status": "REVOKED" if body.overlap_seconds == 0 else "ACTIVE",
                },
                body.revision,
            )
            await save(
                uow,
                "key_rotations",
                rotation_id,
                {
                    "old_key_id": old_key_id,
                    "new_key_id": key_id,
                    "overlap_until": overlap_until,
                    "operator_id": session.account.id,
                },
            )
            if body.overlap_seconds == 0:
                revocation = await enqueue(uow, revoke_id, "key", old_key_id)
            await self.channels.event(uow, event_id, session, "key:rotate", "key", row)
            view = await self.view(uow.connection, row)
        if revocation:
            await self.iam.revocations.complete(revocation)
        return KeyCreated(key=view, api_key=secret, overlap_until=overlap_until)

    async def revoke(
        self, session: AdminSession, channel_id: str, key_id: str, revision: int
    ) -> KeyView:
        await self.channels.authorize(session, channel_id, "key:manage")
        event_id, revoke_id = new_id("audit"), new_id("revoke")
        keys = self.channels.keys(channel_id, "channel_keys", key_id, event_id) + [
            record_key(channel_id, "iam_revocations", revoke_id)
        ]
        async with transaction(
            self.repository.engine, self.channels.scope(session, channel_id), keys
        ) as uow:
            await self.channels.locked(uow, session, "key:manage")
            old = await required(uow.connection, "channel_keys", channel_id, id=key_id)
            await required(uow.connection, "service_clients", channel_id, id=old["client_id"])
            await self.channels.require_visible(
                uow.connection,
                session,
                old["environment"],
                action="key:manage",
            )
            if old["status"] == "REVOKED":
                raise ServiceError("INVALID_STATE", "Key 已吊销", 409)
            row = await save(uow, "channel_keys", key_id, {"status": "REVOKED"}, revision)
            revocation = await enqueue(uow, revoke_id, "key", key_id)
            await self.channels.event(
                uow, event_id, session, "key:revoke", "key", row, old["status"]
            )
            view = await self.view(uow.connection, row)
        await self.iam.revocations.complete(revocation)
        return view

    async def exchange(self, body: TokenExchange, request_id: str) -> TokenResponse:
        try:
            return await self._exchange(body, request_id)
        except ServiceError as exc:
            if exc.status in {401, 403, 404}:
                event_id = new_id("audit")
                async with transaction(
                    self.repository.engine,
                    ControlScope(purpose="identity_lookup", actor_id="key_authentication"),
                    [record_key("system", "audit_events", event_id)],
                ) as uow:
                    await append_event(
                        uow,
                        event_id,
                        "unauthenticated",
                        request_id,
                        "auth:token",
                        "key",
                        "unidentified",
                        outcome="DENIED",
                    )
            raise

    async def _exchange(self, body: TokenExchange, request_id: str) -> TokenResponse:
        digest = secret_digest(body.api_key.get_secret_value())
        async with self.repository.engine.connect() as connection:
            index = await self.repository.locate_key(connection, digest)
            if index is None or index["target_channel_id"] == "system":
                raise ServiceError("UNAUTHENTICATED", "接入凭据无效", 401)
            channel_id, key_id = index["target_channel_id"], index["key_id"]
            key = await one(connection, "channel_keys", channel_id, id=key_id)
            if key is None or not hmac.compare_digest(digest, key["secret_digest"]):
                raise ServiceError("UNAUTHENTICATED", "接入凭据无效", 401)
        context = AuthContext(
            scope=Scope(channel_id=channel_id, environment=key["environment"]),
            principal_type="service",
            principal_id=key["client_id"],
            client_id=key["client_id"],
            key_id=key_id,
            request_id=request_id,
        )
        event_id = new_id("audit")
        async with transaction(
            self.repository.engine,
            context.scope,
            self.channels.keys(channel_id, "channel_keys", key_id, event_id),
        ) as uow:
            await current_service(uow.connection, context)
            channel = await required(uow.connection, "channels", channel_id, id=channel_id)
            environment = await required(
                uow.connection, "channel_environments", channel_id, environment=key["environment"]
            )
            require_available(channel, environment)
            key = await required(uow.connection, "channel_keys", channel_id, id=key_id)
            await self.repository.record_use(uow, key_id)
            await append_event(
                uow, event_id, key["client_id"], request_id, "auth:token", "key", key_id
            )
        # Redis 调用在短事务提交后进行，签发前后均由 IAM 复核实时状态。
        return await self.iam.sessions.issue_service(context)
