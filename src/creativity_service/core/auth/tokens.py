"""认证 Redis 是会话真值；脚本原子维护有效期及撤销索引。"""

import hashlib
import math
import secrets
from collections.abc import Awaitable
from datetime import datetime, timedelta
from typing import Any, cast

from pydantic import ValidationError
from redis.asyncio import Redis
from redis.exceptions import RedisError

from creativity_service.core.auth.types import Purpose, Revocation, TokenRecord, TokenResponse
from creativity_service.core.context import Environment
from creativity_service.core.database import assert_external_io_allowed
from creativity_service.core.primitives import ServiceError, new_id, unavailable, utcnow

# 会话标识与身份绑定签发后不变，续时不影响替换；竞争切换仍只能成功一次。
ISSUE_SCRIPT = """
local old = nil
if ARGV[4] ~= '' then
  local raw = redis.call('GET', KEYS[2])
  if not raw or redis.call('PTTL', KEYS[2]) <= 0 then
    return 0
  end
  old = cjson.decode(raw)
  if old.session_id ~= ARGV[4] then return 0 end
end
if redis.call('EXISTS', KEYS[1]) ~= 0 then return -1 end
local now = redis.call('TIME')
local ttl = tonumber(ARGV[2]) - tonumber(now[1]) * 1000 - math.floor(tonumber(now[2]) / 1000)
if ttl <= 0 then return 0 end
if old then
  local deadline = tonumber(old.expires_at) or tonumber(ARGV[5])
  if deadline * 1000 <= tonumber(now[1]) * 1000 + math.floor(tonumber(now[2]) / 1000) then
    return 0
  end
end
redis.call('SET', KEYS[1], ARGV[1], 'PX', ttl)
local current = cjson.decode(ARGV[1])
for _, idx in ipairs(current.index_keys) do
  redis.call('ZREMRANGEBYSCORE', idx, '-inf', now[1])
  redis.call('ZADD', idx, ARGV[3], current.token_digest)
  local last = redis.call('ZREVRANGE', idx, 0, 0, 'WITHSCORES')
  redis.call('EXPIREAT', idx, math.ceil(tonumber(last[2])))
end
if old then
  redis.call('DEL', KEYS[2])
  for _, idx in ipairs(old.index_keys) do redis.call('ZREM', idx, old.token_digest) end
end
return 1
"""

READ_SCRIPT = """
local value = redis.call('GET', KEYS[1])
local ttl = redis.call('PTTL', KEYS[1])
if not value or ttl <= 0 then return nil end
return value
"""

# 身份校验成功后续时；会话标识保持不变，删除与续时互斥，旧请求不能重建会话。
# 到期时间使用时间戳写回同一字段，兼容既有 ISO 时间串；TTL 与撤销索引一起更新。
RENEW_SCRIPT = """
local raw = redis.call('GET', KEYS[1])
if not raw or redis.call('PTTL', KEYS[1]) <= 0 then return nil end
local current = cjson.decode(raw)
if current.session_id ~= ARGV[1] then return nil end
local time = redis.call('TIME')
local now = tonumber(time[1]) + math.floor(tonumber(time[2]) / 1000) / 1000
local deadline = tonumber(current.expires_at) or tonumber(ARGV[2])
if deadline <= now then return nil end
deadline = math.max(deadline, now + tonumber(ARGV[3]))
if ARGV[4] ~= '' then deadline = math.min(deadline, tonumber(ARGV[4])) end
if deadline <= now then return nil end
current.expires_at = deadline
local value = cjson.encode(current)
redis.call('SET', KEYS[1], value, 'PX', math.max(1, math.floor((deadline - now) * 1000)))
for _, idx in ipairs(current.index_keys) do
  redis.call('ZREMRANGEBYSCORE', idx, '-inf', now)
  redis.call('ZADD', idx, deadline, current.token_digest)
  local last = redis.call('ZREVRANGE', idx, 0, 0, 'WITHSCORES')
  redis.call('EXPIREAT', idx, math.ceil(tonumber(last[2])))
end
return value
"""

REVOKE_SCRIPT = """
local digests = {}
if ARGV[1] == 'token' then
  digests = {ARGV[2]}
else
  digests = redis.call('ZRANGE', KEYS[1], 0, -1)
end
for _, digest in ipairs(digests) do
  local key = ARGV[3] .. digest
  local raw = redis.call('GET', key)
  if raw then
    local value = cjson.decode(raw)
    if value.issued_at_ms <= tonumber(ARGV[4]) then
      redis.call('DEL', key)
      for _, idx in ipairs(value.index_keys) do redis.call('ZREM', idx, digest) end
    end
  elseif ARGV[1] ~= 'token' then
    redis.call('ZREM', KEYS[1], digest)
  end
end
return 1
"""

# 身份尚未确定的限速键归系统渠道；不信任客户端转发的 IP 头。
LIMIT_SCRIPT = """
local denied = 0
for i, key in ipairs(KEYS) do
  local count = redis.call('INCR', key)
  if count == 1 or redis.call('TTL', key) < 0 then redis.call('EXPIRE', key, ARGV[1]) end
  if count > tonumber(ARGV[i+1]) then denied = 1 end
end
return denied
"""


class TokenStore:
    def __init__(
        self, redis: Redis, prefix: str, management_ttl: int = 28800, service_ttl: int = 3600
    ) -> None:
        self.redis, self.prefix = redis, f"{prefix}:auth"
        self.management_ttl, self.service_ttl = management_ttl, service_ttl

    async def _eval(self, script: str, count: int, *args: str | int | float) -> Any:
        # redis 的共享类型同时描述同步/异步客户端；此处只接收异步 Redis。
        return await cast(Awaitable[Any], self.redis.eval(script, count, *(str(a) for a in args)))

    def token_key(self, digest: str) -> str:
        return f"{self.prefix}:token:{digest}"

    def index_key(self, channel_id: str, kind: str, target_id: str) -> str:
        return f"{self.prefix}:index:{channel_id}:{kind}:{target_id}"

    @staticmethod
    def digest(token: str) -> str:
        return hashlib.sha256(token.encode()).hexdigest()

    async def issue(
        self,
        *,
        purpose: Purpose,
        principal_id: str,
        channel_id: str = "system",
        environment: Environment | None = None,
        data_scope_id: str | None = None,
        credential_version: int | None = None,
        membership_version: int | None = None,
        client_id: str | None = None,
        key_id: str | None = None,
        expires_by: datetime | None = None,
        upstream_expires_at: datetime | None = None,
        identity_channel_id: str | None = None,
        replace: TokenRecord | None = None,
        must_change_password: bool = False,
    ) -> tuple[TokenResponse, TokenRecord]:
        assert_external_io_allowed()
        now = utcnow()
        ttl = self.service_ttl if purpose == "service" else self.management_ttl
        expires_at = now + timedelta(seconds=ttl)
        if replace and replace.upstream_expires_at:
            upstream_expires_at = replace.upstream_expires_at
            identity_channel_id = replace.identity_channel_id
        if identity_channel_id and identity_channel_id != channel_id:
            raise ServiceError("TOKEN_SCOPE_INVALID", "外部身份会话只能进入原授权渠道", 403)
        if upstream_expires_at is not None:
            expires_at = min(expires_at, upstream_expires_at)
        if expires_by is not None:
            expires_at = min(expires_at, expires_by)
        if expires_at <= now:
            raise ServiceError("UNAUTHENTICATED", "凭据已到期", 401)
        secret = secrets.token_urlsafe(32)
        digest = self.digest(secret)
        indexes = (
            [self.index_key(channel_id, "key", key_id)]
            if key_id
            else [self.index_key("system", "account", principal_id)]
        )
        if purpose == "management":
            indexes.append(self.index_key(channel_id, "member", principal_id))
        record = TokenRecord(
            upstream_expires_at=upstream_expires_at,
            identity_channel_id=identity_channel_id,
            channel_id=channel_id,
            token_digest=digest,
            session_id=new_id("session"),
            purpose=purpose,
            principal_type="service" if purpose == "service" else "management",
            principal_id=principal_id,
            environment=environment,
            data_scope_id=data_scope_id,
            credential_version=credential_version,
            membership_version=membership_version,
            client_id=client_id,
            key_id=key_id,
            issued_at=now,
            issued_at_ms=math.floor(now.timestamp() * 1000),
            expires_at=expires_at,
            index_keys=indexes,
        )
        if replace and (replace.principal_id != principal_id or replace.purpose == "service"):
            raise ServiceError("TOKEN_PURPOSE_INVALID", "不能替换其他身份会话", 401)
        try:
            result = await self._eval(
                ISSUE_SCRIPT,
                2,
                self.token_key(digest),
                self.token_key(replace.token_digest) if replace else self.token_key(digest),
                record.model_dump_json(),
                math.floor(expires_at.timestamp() * 1000),
                expires_at.timestamp(),
                replace.session_id if replace else "",
                replace.expires_at.timestamp() if replace else "",
            )
        except RedisError as exc:
            raise unavailable("认证存储") from exc
        if result != 1:
            raise ServiceError("UNAUTHENTICATED", "会话已变更，请重新登录", 401)
        return TokenResponse(
            access_token=secret,
            expires_in=math.floor((expires_at - now).total_seconds()),
            expires_at=expires_at,
            must_change_password=must_change_password,
        ), record

    async def read(self, token: str, purposes: set[str]) -> TokenRecord:
        if not 32 <= len(token) <= 512:
            raise ServiceError("UNAUTHENTICATED", "请重新登录", 401)
        return await self.read_digest(self.digest(token), purposes)

    async def read_digest(self, digest: str, purposes: set[str]) -> TokenRecord:
        assert_external_io_allowed()
        try:
            raw = await self._eval(READ_SCRIPT, 1, self.token_key(digest))
        except RedisError as exc:
            raise unavailable("认证存储") from exc
        return self._parse_record(raw, digest, purposes)

    @staticmethod
    def _parse_record(raw: Any, digest: str, purposes: set[str]) -> TokenRecord:
        try:
            record = TokenRecord.model_validate_json(raw) if raw else None
        except (ValidationError, TypeError, ValueError):
            record = None
        if (
            record is None
            or record.token_digest != digest
            or record.expires_at <= utcnow()
            or record.issued_at > utcnow()
        ):
            raise ServiceError("UNAUTHENTICATED", "请重新登录", 401)
        if record.purpose not in purposes:
            raise ServiceError("TOKEN_PURPOSE_INVALID", "凭据用途不符", 401)
        return record

    async def renew(self, record: TokenRecord) -> TokenRecord:
        """复用已验证身份，只续管理会话；外部登录仍受原始凭据到期时间限制。"""
        if record.purpose == "service":
            return record
        assert_external_io_allowed()
        try:
            raw = await self._eval(
                RENEW_SCRIPT,
                1,
                self.token_key(record.token_digest),
                record.session_id,
                record.expires_at.timestamp(),
                self.management_ttl,
                record.upstream_expires_at.timestamp() if record.upstream_expires_at else "",
            )
        except RedisError as exc:
            raise unavailable("认证存储") from exc
        return self._parse_record(raw, record.token_digest, {record.purpose})

    async def revoke(self, revocation: Revocation) -> None:
        assert_external_io_allowed()
        try:
            await self._eval(
                REVOKE_SCRIPT,
                1,
                self.index_key(revocation.channel_id, revocation.kind, revocation.target_id),
                revocation.kind,
                revocation.target_id,
                f"{self.prefix}:token:",
                math.floor(revocation.cutoff_at.timestamp() * 1000),
            )
        except RedisError as exc:
            raise unavailable("认证撤销存储") from exc

    async def limit_login(self, login_name: str, remote_ip: str) -> None:
        assert_external_io_allowed()
        keys = [
            f"{self.prefix}:limit:system:{kind}:{self.digest(value)}"
            for kind, value in (("login", login_name), ("ip", remote_ip))
        ]
        try:
            denied = await self._eval(LIMIT_SCRIPT, 2, *keys, 300, 10, 60)
        except RedisError as exc:
            raise unavailable("登录限速服务") from exc
        if denied:
            raise ServiceError("LOGIN_RATE_LIMITED", "登录尝试过于频繁，请稍后重试", 429)
