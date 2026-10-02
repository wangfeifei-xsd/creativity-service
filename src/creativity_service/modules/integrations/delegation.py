"""先验签再解析源数据域；nonce 防重放不替代运行受理的幂等事务。"""

from datetime import UTC, datetime, timedelta
from typing import Any, Protocol

from fastapi import Request
from pydantic import ValidationError
from sqlalchemy.ext.asyncio import AsyncConnection

from creativity_service.core.auth.types import SubjectAuthority
from creativity_service.core.context import AuthContext, Scope
from creativity_service.core.database import transaction
from creativity_service.core.locking import record_key
from creativity_service.core.primitives import ServiceError, digest, unavailable, utcnow
from creativity_service.integrations.business.delegation import (
    HEADER,
    DelegationClaims,
    RequestBinding,
    bind_request,
    split_envelope,
    verify_payload,
)
from creativity_service.modules.channels.state import current_service
from creativity_service.modules.iam.repositories import policy_key
from creativity_service.modules.integrations.keys import DelegationKeys
from creativity_service.modules.integrations.repositories import (
    configuration_key,
    environment_scope,
    repository,
    source_mapping,
)

ANONYMOUS_ACTIONS = frozenset({"run:create", "run:read"})


class CurrentSubjectReader(Protocol):
    async def read_current(
        self, context: AuthContext, claims: DelegationClaims
    ) -> SubjectAuthority:
        """由源服务复核当前主体权限；不从用户请求或模型输入取得授权。"""
        ...


class DelegationService:
    def __init__(
        self, keys: DelegationKeys, current_subjects: CurrentSubjectReader | None = None
    ) -> None:
        self.keys, self.engine, self.current_subjects = keys, keys.engine, current_subjects
        self.authentication = keys.authorization.authentication

    async def key_record(
        self, connection: AsyncConnection, context: AuthContext, kid: str
    ) -> dict[str, Any]:
        row = await repository(environment_scope(context.scope), "delegation_keys").get(
            connection, kid
        )
        if (
            row is None
            or row["client_id"] != context.client_id
            or row["algorithm"] != "HMAC-SHA256"
            or row["status"] != "ACTIVE"
            or row["not_before"] > utcnow()
            or row["expires_at"] <= utcnow()
        ):
            raise ServiceError("DELEGATION_INVALID", "业务主体委托无效", 401)
        return row

    def validate_claims(
        self, context: AuthContext, claims: DelegationClaims, key: dict[str, Any]
    ) -> None:
        now, skew = utcnow().timestamp(), key["clock_skew_seconds"]
        if (
            claims.issued_at > now + skew
            or claims.expires_at <= now - skew
            or claims.expires_at <= claims.issued_at
            or claims.expires_at - claims.issued_at > key["max_ttl_seconds"]
            or claims.issued_at < key["not_before"].timestamp() - skew
        ):
            raise ServiceError("DELEGATION_EXPIRED", "业务主体委托已过期或签发时间无效", 401)
        if claims.issuer != key["issuer"] or claims.audience != key["audience"]:
            raise ServiceError("DELEGATION_INVALID", "业务主体委托无效", 401)
        if (claims.channel_id is not None and claims.channel_id != context.scope.channel_id) or (
            claims.environment is not None and claims.environment != context.scope.environment
        ):
            raise ServiceError("DELEGATION_SCOPE_INVALID", "业务主体委托归属不符", 403)
        if (
            len(claims.actions) != len(set(claims.actions))
            or not claims.resources
            or len(claims.resources) > 16
            or any(
                not refs or len(refs) > 128 or len(refs) != len(set(refs))
                for refs in claims.resources.values()
            )
        ):
            raise ServiceError("DELEGATION_INVALID", "业务主体委托权限无效", 401)
        if claims.subject_type == "anonymous" and (
            not set(claims.actions) <= ANONYMOUS_ACTIONS
            or not set(claims.resources) <= {"agent", "run"}
        ):
            raise ServiceError("DELEGATION_FORBIDDEN", "匿名主体仅可使用公开只读能力", 403)

    async def verify(
        self, context: AuthContext, envelope: str, request: RequestBinding
    ) -> AuthContext:
        if context.principal_type != "service" or not context.client_id:
            raise ServiceError("TOKEN_PURPOSE_INVALID", "主体委托需要服务 Token", 401)
        await self.authentication.revalidate(context)
        kid, payload, signature = split_envelope(envelope)
        # 不使用声明中的渠道、环境、issuer 或主体选择密钥。
        async with self.engine.connect() as connection:
            key = await self.key_record(connection, context, kid)
        secret = await self.keys.secret(context, key)
        claims = verify_payload(kid, payload, signature, secret)
        del secret
        self.validate_claims(context, claims, key)
        if claims.request != request:
            raise ServiceError("DELEGATION_REQUEST_MISMATCH", "委托与实际请求不一致", 403)
        if (
            request.method == "POST"
            and request.target == "/api/v1/runs"
            and not request.idempotency_key
        ):
            raise ServiceError("IDEMPOTENCY_KEY_REQUIRED", "创建运行需要幂等键", 422)
        scope = environment_scope(context.scope)
        nonce_id = digest([scope.channel_id, scope.environment, context.client_id, claims.nonce])
        async with transaction(
            self.engine,
            scope,
            [
                configuration_key(scope),
                policy_key(scope.channel_id),
                record_key(scope.channel_id, "delegation_nonces", nonce_id),
            ],
        ) as uow:
            current = await self.key_record(uow.connection, context, kid)
            if current["revision"] != key["revision"]:
                raise ServiceError("DELEGATION_KEY_CHANGED", "委托密钥已变化，请重试", 409)
            self.validate_claims(context, claims, current)
            identity = await current_service(uow.connection, context)
            if not set(claims.actions) <= identity.client_actions & identity.key_actions:
                raise ServiceError("DELEGATION_FORBIDDEN", "业务主体委托超过接入服务授权", 403)
            domain = await source_mapping(
                uow.connection, scope, claims.data_scope.type, claims.data_scope.id
            )
            if domain["id"] not in identity.data_scopes:
                raise ServiceError("DELEGATION_SCOPE_INVALID", "业务主体数据域未获授权", 403)
            resolved = Scope(
                channel_id=scope.channel_id,
                environment=scope.environment,
                data_scope_id=domain["id"],
                subject_type=claims.subject_type,
                subject_id=claims.subject_id,
            )
            repo = repository(scope, "delegation_nonces")
            old = await repo.get(uow.connection, nonce_id)
            claims_digest, request_digest = (
                digest(claims.model_dump(mode="json")),
                digest(request.model_dump()),
            )
            if old:
                if (
                    old["kid"] != kid
                    or old["claims_digest"] != claims_digest
                    or old["request_digest"] != request_digest
                    or old["resolved_scope"] != resolved.model_dump()
                ):
                    raise ServiceError("DELEGATION_REPLAY", "委托随机数已用于不同请求或身份", 409)
            else:
                expires = datetime.fromtimestamp(claims.expires_at + key["clock_skew_seconds"], UTC)
                await repo.add(
                    uow,
                    nonce_id,
                    {
                        "client_id": context.client_id,
                        "kid": kid,
                        "nonce_digest": digest(claims.nonce),
                        "request_digest": request_digest,
                        "claims_digest": claims_digest,
                        "claims": claims.model_dump(mode="json"),
                        "resolved_scope": resolved.model_dump(),
                        "expires_at": expires,
                        "retain_until": max(expires, utcnow() + timedelta(days=1)),
                    },
                )
        return context.model_copy(
            update={
                "scope": resolved,
                "delegation_id": nonce_id,
                "granted_actions": frozenset(claims.actions),
            }
        )

    async def verify_request(self, context: AuthContext, request: Request) -> AuthContext:
        values = request.headers.getlist(HEADER)
        idem = request.headers.getlist("Idempotency-Key")
        if len(values) != 1 or len(idem) > 1:
            raise ServiceError("DELEGATION_REQUIRED", "请提供业务后端签发的主体委托", 401)
        body = await request.body()
        if len(body) > 2 * 1024 * 1024:
            raise ServiceError("REQUEST_TOO_LARGE", "请求正文超过上限", 413)
        try:
            target = request.scope.get("raw_path", request.url.path.encode()).decode("ascii")
            query = request.scope.get("query_string", b"").decode("ascii")
            binding = bind_request(
                request.method,
                target + ("?" + query if query else ""),
                body,
                idem[0] if idem else None,
            )
        except (ValueError, ValidationError, UnicodeError):
            raise ServiceError("DELEGATION_INVALID", "请求绑定无效", 401) from None
        return await self.verify(context, values[0], binding)

    async def read_current(self, context: AuthContext) -> SubjectAuthority:
        if not context.delegation_id or not context.client_id:
            raise ServiceError("DELEGATION_REQUIRED", "缺少已验证的业务主体委托", 401)
        scope = environment_scope(context.scope)
        async with self.engine.connect() as connection:
            row = await repository(scope, "delegation_nonces").get(
                connection, context.delegation_id
            )
            if (
                row is None
                or row["client_id"] != context.client_id
                or row["resolved_scope"] != context.scope.model_dump()
            ):
                raise ServiceError("DELEGATION_INVALID", "主体委托归属不符", 401)
            await self.key_record(connection, context, row["kid"])
            claims = DelegationClaims.model_validate(row["claims"])
            domain = await source_mapping(
                connection, scope, claims.data_scope.type, claims.data_scope.id
            )
            if domain["id"] != context.scope.data_scope_id:
                raise ServiceError("DELEGATION_SCOPE_INVALID", "业务主体数据域已变化", 403)
        authority = SubjectAuthority(
            scope=context.scope,
            expires_at=row["expires_at"],
            actions=frozenset(claims.actions),
            agent_actions=frozenset(claims.actions),
            resources={k: frozenset(v) for k, v in claims.resources.items()},
        )
        if context.principal_type == "worker":
            # 后台任务不能续用已过期的浏览器/服务声明；源业务适配器必须复核当前权限。
            if self.current_subjects is None:
                raise unavailable("业务主体当前权限复核")
            current = await self.current_subjects.read_current(context, claims)
            if (
                current.scope != context.scope
                or current.expires_at <= utcnow()
                or not current.actions <= authority.actions
                or not current.agent_actions <= authority.agent_actions
                or any(
                    "*" not in authority.resources.get(k, frozenset())
                    and not refs <= authority.resources.get(k, frozenset())
                    for k, refs in current.resources.items()
                )
            ):
                raise ServiceError("DELEGATION_FORBIDDEN", "主体当前权限与原委托不符", 403)
            return current
        if authority.expires_at <= utcnow():
            raise ServiceError("DELEGATION_EXPIRED", "业务主体委托已过期", 401)
        return authority
