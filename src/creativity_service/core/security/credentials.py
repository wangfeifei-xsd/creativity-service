"""密文与密钥版本分离；明文仅进入受控调用边界。"""

import os
from collections.abc import Awaitable, Callable
from typing import Literal, Protocol

from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from pydantic import SecretBytes
from sqlalchemy.ext.asyncio import AsyncEngine

from creativity_service.core.context import AuthContext, Authorization, DenyAuthorization
from creativity_service.core.database import Repository, assert_external_io_allowed, transaction
from creativity_service.core.database.tables import metadata
from creativity_service.core.locking import record_key
from creativity_service.core.primitives import ServiceError, canonical_json, new_id, unavailable


class KeyProvider(Protocol):
    async def current(self) -> tuple[str, SecretBytes]: ...
    async def resolve(self, version: str) -> SecretBytes: ...


class CredentialService:
    def __init__(
        self,
        engine: AsyncEngine,
        keys: KeyProvider | None = None,
        authorization: Authorization | None = None,
    ) -> None:
        self.engine, self.keys = engine, keys
        self.authorization = authorization or DenyAuthorization()

    @staticmethod
    def aad(context: AuthContext, credential_id: str, purpose: str) -> bytes:
        return canonical_json(
            [context.scope.channel_id, context.scope.environment, credential_id, purpose]
        )

    async def store(
        self,
        context: AuthContext,
        purpose: Literal["model", "mcp", "http_tool", "delegation", "webhook"],
        plaintext: SecretBytes,
    ) -> str:
        assert_external_io_allowed()
        await self.authorization.require(context, "credential:write", purpose)
        if self.keys is None:
            raise unavailable("凭据密钥服务")
        version, key = await self.keys.current()
        credential_id = new_id("credential")
        nonce = os.urandom(12)
        encrypted = AESGCM(key.get_secret_value()).encrypt(
            nonce, plaintext.get_secret_value(), self.aad(context, credential_id, purpose)
        )
        repository = Repository(metadata.tables["credentials"], context.scope)
        async with transaction(
            self.engine,
            context.scope,
            [record_key(context.scope.channel_id, "credentials", credential_id)],
        ) as uow:
            await repository.add(
                uow,
                credential_id,
                {
                    "purpose": purpose,
                    "ciphertext": nonce + encrypted,
                    "key_version": version,
                    "state": "ACTIVE",
                },
            )
        return credential_id

    async def call[T](
        self,
        context: AuthContext,
        credential_id: str,
        purpose: str,
        operation: Callable[[SecretBytes], Awaitable[T]],
    ) -> T:
        assert_external_io_allowed()
        if self.keys is None:
            raise unavailable("凭据密钥服务")
        await self.authorization.require(context, "credential:use", credential_id)
        repository = Repository(metadata.tables["credentials"], context.scope)
        async with self.engine.connect() as connection:
            row = await repository.get(connection, credential_id)
        if row is None or row["purpose"] != purpose or row["state"] != "ACTIVE":
            raise ServiceError("CREDENTIAL_UNAVAILABLE", "凭据不可用", 403)
        key = await self.keys.resolve(row["key_version"])
        try:
            data = bytes(row["ciphertext"])
            plaintext = SecretBytes(
                AESGCM(key.get_secret_value()).decrypt(
                    data[:12], data[12:], self.aad(context, credential_id, purpose)
                )
            )
        except Exception as exc:
            raise ServiceError("CREDENTIAL_DECRYPT_FAILED", "凭据无法解密", 503) from exc
        # 密钥读取可能等待远端；发送前再次核查当前授权与凭据启用状态。
        await self.authorization.require(context, "credential:use", credential_id)
        async with self.engine.connect() as connection:
            current = await repository.get(connection, credential_id)
        if (
            current is None
            or current["state"] != "ACTIVE"
            or current["revision"] != row["revision"]
        ):
            raise ServiceError("CREDENTIAL_UNAVAILABLE", "凭据不可用", 403)
        try:
            return await operation(plaintext)
        finally:
            # Python 对象无法承诺内存擦除；只限制引用生命周期，不将明文返回存储层。
            del plaintext
