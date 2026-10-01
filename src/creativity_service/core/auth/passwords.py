"""口令摘要与登录名规范化；耗时摘要计算在事务外执行。"""

import asyncio
import hashlib
import hmac
import re
import secrets
import unicodedata

from creativity_service.core.database import assert_external_io_allowed
from creativity_service.core.primitives import ServiceError

ITERATIONS = 600_000
DUMMY_HASH = f"pbkdf2_sha256${ITERATIONS}${'00' * 32}${'00' * 32}"


def normalize_login(value: str) -> str:
    value = unicodedata.normalize("NFKC", value).strip().casefold()
    if not re.fullmatch(r"[a-z0-9][a-z0-9._@+\-]{2,127}", value):
        raise ServiceError("LOGIN_NAME_INVALID", "登录名须为 3 至 128 位字母、数字或常用符号", 422)
    return value


def validate_password(value: str) -> None:
    if not 12 <= len(value) <= 256:
        raise ServiceError("PASSWORD_INVALID", "密码长度须为 12 至 256 个字符", 422)


class PasswordHasher:
    def __init__(self) -> None:
        self.slots = asyncio.Semaphore(4)

    async def hash(self, value: str) -> str:
        assert_external_io_allowed()
        validate_password(value)
        salt = secrets.token_bytes(32)
        async with self.slots:
            result = await asyncio.to_thread(
                hashlib.pbkdf2_hmac, "sha256", value.encode(), salt, ITERATIONS
            )
        return f"pbkdf2_sha256${ITERATIONS}${salt.hex()}${result.hex()}"

    async def verify(self, value: str, encoded: str | None) -> bool:
        assert_external_io_allowed()
        if len(value) > 256:
            return False
        try:
            algorithm, iterations, salt, expected = (encoded or DUMMY_HASH).split("$")
            if algorithm != "pbkdf2_sha256" or int(iterations) != ITERATIONS:
                return False
            salt_bytes, expected_bytes = bytes.fromhex(salt), bytes.fromhex(expected)
            if len(salt_bytes) != 32 or len(expected_bytes) != 32:
                return False
        except (ValueError, TypeError):
            return False
        async with self.slots:
            actual = await asyncio.to_thread(
                hashlib.pbkdf2_hmac, "sha256", value.encode(), salt_bytes, ITERATIONS
            )
        return hmac.compare_digest(actual, expected_bytes) and encoded is not None
