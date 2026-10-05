"""登录滑块验证：系统渠道隔离、限速、短时挑战与一次性登录凭据。"""

import asyncio
import secrets
import time
from collections.abc import Awaitable
from typing import Any, Literal, cast

from pydantic import ValidationError
from redis.asyncio import Redis
from redis.exceptions import RedisError

from creativity_service.core.auth.passwords import normalize_login
from creativity_service.core.auth.tokens import LIMIT_SCRIPT, TokenStore
from creativity_service.core.database import assert_external_io_allowed
from creativity_service.core.primitives import Contract, ServiceError, unavailable
from creativity_service.modules.iam.captcha_image import HEIGHT, PIECE_SIZE, WIDTH, render_puzzle
from creativity_service.modules.iam.schemas import (
    CaptchaChallenge,
    CaptchaVerification,
    CaptchaVerifyInput,
)

# 原子消费并检查 TTL；失败的尝试同样销毁挑战，并发请求不能复用。
CONSUME_SCRIPT = """
local ttl = redis.call('PTTL', KEYS[1])
local value = redis.call('GETDEL', KEYS[1])
if ttl <= 0 then return nil end
return value
"""
CHALLENGE_TTL, PROOF_TTL = 120, 60


class CaptchaRecord(Contract):
    channel_id: Literal["system"] = "system"
    login_digest: str
    remote_digest: str
    issued_at: float
    target: int | None = None


class CaptchaService:
    def __init__(self, redis: Redis, prefix: str) -> None:
        self.redis, self.prefix = redis, f"{prefix}:auth:captcha:system"

    def key(self, kind: str, secret: str) -> str:
        return f"{self.prefix}:{kind}:{TokenStore.digest(secret)}"

    async def _eval(self, script: str, count: int, *args: str | int) -> Any:
        assert_external_io_allowed()
        try:
            return await cast(
                Awaitable[Any], self.redis.eval(script, count, *(str(a) for a in args))
            )
        except RedisError as exc:
            raise unavailable("安全验证服务") from exc

    async def _limit(self, remote_ip: str, kind: str, maximum: int) -> None:
        if await self._eval(LIMIT_SCRIPT, 1, self.key(f"limit-{kind}", remote_ip), 60, maximum):
            raise ServiceError("CAPTCHA_RATE_LIMITED", "验证请求过于频繁，请稍后重试", 429)

    async def _store(self, kind: str, record: CaptchaRecord, ttl: int) -> str:
        assert_external_io_allowed()
        secret = secrets.token_urlsafe(32)
        try:
            stored = await self.redis.set(
                self.key(kind, secret), record.model_dump_json(), ex=ttl, nx=True
            )
        except RedisError as exc:
            raise unavailable("安全验证服务") from exc
        if not stored:
            raise unavailable("安全验证服务")
        return secret

    async def _consume(self, kind: str, secret: str) -> CaptchaRecord:
        raw = await self._eval(CONSUME_SCRIPT, 1, self.key(kind, secret))
        if raw is None:
            raise ServiceError("CAPTCHA_EXPIRED", "验证已失效，请重新验证", 400)
        try:
            return CaptchaRecord.model_validate_json(raw)
        except ValidationError as exc:
            raise ServiceError("CAPTCHA_EXPIRED", "验证已失效，请重新验证", 400) from exc

    async def challenge(self, login_name: str, remote_ip: str) -> CaptchaChallenge:
        name = normalize_login(login_name)
        await self._limit(remote_ip, "challenge", 30)
        x, y = 110 + secrets.randbelow(145), 28 + secrets.randbelow(64)
        background, piece = await asyncio.to_thread(render_puzzle, x, y)
        secret = await self._store(
            "challenge",
            CaptchaRecord(
                login_digest=TokenStore.digest(name),
                remote_digest=TokenStore.digest(remote_ip),
                issued_at=time.time(),
                target=x,
            ),
            CHALLENGE_TTL,
        )
        return CaptchaChallenge(
            challenge_id=secret,
            background=background,
            piece=piece,
            width=WIDTH,
            height=HEIGHT,
            piece_size=PIECE_SIZE,
            piece_y=y,
            expires_in=CHALLENGE_TTL,
        )

    async def verify(self, body: CaptchaVerifyInput, remote_ip: str) -> CaptchaVerification:
        await self._limit(remote_ip, "verify", 60)
        record = await self._consume("challenge", body.challenge_id)
        elapsed = time.time() - record.issued_at
        if (
            record.remote_digest != TokenStore.digest(remote_ip)
            or record.target is None
            or abs(record.target - body.offset) > 4
            or not 0.35 <= elapsed <= CHALLENGE_TTL
        ):
            raise ServiceError("CAPTCHA_FAILED", "拼图未对齐，请重新验证", 400)
        proof = await self._store(
            "proof", record.model_copy(update={"target": None, "issued_at": time.time()}), PROOF_TTL
        )
        return CaptchaVerification(captcha_token=proof, expires_in=PROOF_TTL)

    async def consume(self, proof: str, login_name: str, remote_ip: str) -> None:
        record = await self._consume("proof", proof)
        if (
            record.login_digest != TokenStore.digest(normalize_login(login_name))
            or record.remote_digest != TokenStore.digest(remote_ip)
            or not 0 <= time.time() - record.issued_at <= PROOF_TTL
        ):
            raise ServiceError("CAPTCHA_EXPIRED", "验证已失效，请重新验证", 400)
