"""MCP 凭据直接保存，其他用途使用版本化加密；读取均经过当前授权。"""

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
        purpose: Literal["model", "mcp", "delegation", "webhook"],
        plaintext: SecretBytes,
    ) -> str:
        assert_external_io_allowed()
        await self.authorization.require(context, "credential:write", purpose)
        credential_id = new_id("credential")
        value, ciphertext, version = None, None, None
        if purpose == "mcp":
            value = plaintext.get_secret_value().decode("utf-8")
            if not value:
                raise ServiceError("VALIDATION_ERROR", "MCP 凭据不能为空", 422)
        else:
            if self.keys is None:
                raise unavailable("凭据密钥服务")
            version, key = await self.keys.current()
            nonce = os.urandom(12)
            ciphertext = nonce + AESGCM(key.get_secret_value()).encrypt(
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
                    "secret_value": value,
                    "ciphertext": ciphertext,
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
        if purpose != "mcp" and self.keys is None:
            raise unavailable("凭据密钥服务")
        await self.authorization.require(context, "credential:use", credential_id)
        repository = Repository(metadata.tables["credentials"], context.scope)
        async with self.engine.connect() as connection:
            row = await repository.get(connection, credential_id)
        if row is None or row["purpose"] != purpose or row["state"] != "ACTIVE":
            raise ServiceError("CREDENTIAL_UNAVAILABLE", "凭据不可用", 403)
        if purpose == "mcp":
            if not row["secret_value"] or row["ciphertext"] is not None or row["key_version"]:
                raise ServiceError("CREDENTIAL_UNAVAILABLE", "请重新配置 MCP 鉴权凭据", 403)
            plaintext = SecretBytes(row["secret_value"].encode("utf-8"))
        else:
            assert self.keys is not None
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
            # Python 对象无法承诺内存擦除；这里只限制调用侧引用生命周期。
            del plaintext
