"""配置化 MCP 主体复核；只使用绑定的服务凭据，绝不递归授权待复核主体。"""

from typing import Any

from pydantic import SecretBytes, ValidationError
from sqlalchemy.ext.asyncio import AsyncEngine

from creativity_service.core.auth.types import SubjectAuthority
from creativity_service.core.context import AuthContext
from creativity_service.core.database import transaction
from creativity_service.core.locking import record_key
from creativity_service.core.observability.audit import append_audit
from creativity_service.core.primitives import ServiceError, digest, new_id, utcnow
from creativity_service.core.security.credentials import CredentialService
from creativity_service.integrations.business.delegation import DelegationClaims
from creativity_service.modules.channels.repositories import required
from creativity_service.modules.channels.repositories import rows as channel_rows
from creativity_service.modules.iam.authorization import IamAuthorization
from creativity_service.modules.iam.repositories import policy_key
from creativity_service.modules.integrations.authorization import require_management
from creativity_service.modules.integrations.repositories import configuration_key, repository
from creativity_service.modules.integrations.schemas import NamedOption
from creativity_service.modules.integrations.subject_contracts import (
    SubjectReviewRequest,
    SubjectReviewResponse,
    SubjectReviewSave,
    SubjectReviewView,
)
from creativity_service.modules.mcp.repositories import repository as mcp_repository
from creativity_service.modules.mcp.schemas import McpTimeouts, RemoteTool
from creativity_service.modules.mcp.services import McpService
from creativity_service.modules.tools.validation import validate_json


class ConfiguredSubjectReader:
    def __init__(
        self, engine: AsyncEngine, authorization: IamAuthorization, mcp: McpService
    ) -> None:
        self.engine, self.authorization, self.mcp = engine, authorization, mcp

    async def require(self, context: AuthContext) -> None:
        if context.principal_type != "management":
            raise ServiceError("FORBIDDEN", "主体复核配置需要管理身份", 403)
        await self.authorization.boundary(
            context, "integration:manage", "channel", context.scope.channel_id
        )

    async def binding(self, context: AuthContext) -> dict[str, Any]:
        async with self.engine.connect() as connection:
            rows = await repository(context.scope, "subject_review_bindings").find(
                connection, client_id=context.client_id
            )
        if len(rows) != 1 or not rows[0]["enabled"]:
            raise ServiceError("SUBJECT_REVIEW_REQUIRED", "当前环境未配置有效的主体复核", 403)
        return rows[0]

    async def source(
        self, context: AuthContext, binding: dict[str, Any]
    ) -> tuple[dict[str, Any], RemoteTool]:
        row = await self.mcp.get(context, "mcp_connections", binding["connection_id"])
        snapshot = await self.mcp.get(context, "mcp_discoveries", binding["discovery_id"])
        remote = next(
            (
                RemoteTool.model_validate(t)
                for t in snapshot["tool_definitions"]
                if t["name"] == binding["remote_tool_name"]
            ),
            None,
        )
        if (
            row["status"] != "ENABLED"
            or row["auth_failed"]
            or not row["credential_ref"]
            or snapshot["connection_id"] != row["id"]
            or row["configuration_revision"] != binding["connection_revision"]
            or snapshot["connection_revision"] != binding["connection_revision"]
            or remote is None
            or remote.schema_hash != binding["schema_hash"]
            or remote.purpose != "subject_review"
            or remote.annotations.get("readOnlyHint") is not True
            or remote.annotations.get("destructiveHint") is True
        ):
            raise ServiceError(
                "SUBJECT_REVIEW_CHANGED", "主体复核连接或契约已变化，请重新发现并授权", 403
            )
        latest = await self.mcp.rows(context, "mcp_discoveries", connection_id=row["id"])
        if not latest or latest[0]["schema_hashes"].get(remote.name) != remote.schema_hash:
            raise ServiceError("SUBJECT_REVIEW_CHANGED", "主体复核契约已变化", 403)
        await self.mcp.key(context, row)
        return row, remote

    async def view(self, context: AuthContext, row: dict[str, Any]) -> SubjectReviewView:
        async with self.engine.connect() as connection:
            client = await required(
                connection,
                "service_clients",
                context.scope.channel_id,
                id=row["client_id"],
                environment=context.scope.environment,
            )
        mcp = await self.mcp.get(context, "mcp_connections", row["connection_id"])
        reason = None
        try:
            await self.source(context, row)
        except ServiceError as exc:
            reason = exc.message
        if not row["enabled"]:
            reason = "主体复核已停用"
        return SubjectReviewView(
            **{k: row[k] for k in SubjectReviewSave.model_fields},
            binding_id=row["id"],
            client_name=client["name"],
            connection_name=mcp["name"],
            tool_name=row["tool_name"],
            connection_revision=row["connection_revision"],
            schema_hash=row["schema_hash"],
            status_name="可用" if reason is None else "不可用",
            unavailable_reason=reason,
        )

    async def list_bindings(self, context: AuthContext) -> list[SubjectReviewView]:
        await self.require(context)
        async with self.engine.connect() as connection:
            rows = await repository(context.scope, "subject_review_bindings").find(connection)
        return [await self.view(context, row) for row in rows]

    async def clients(self, context: AuthContext) -> list[NamedOption]:
        await self.require(context)
        async with self.engine.connect() as connection:
            rows = await channel_rows(
                connection,
                "service_clients",
                context.scope.channel_id,
                environment=context.scope.environment,
                status="ACTIVE",
            )
        return [
            NamedOption(value=row["id"], label=row["name"])
            for row in rows
            if row["environment"] == context.scope.environment
        ]

    async def save(self, context: AuthContext, body: SubjectReviewSave) -> SubjectReviewView:
        await self.require(context)
        await self.mcp.require(context, body.connection_id)
        scope = context.scope
        binding_id = digest([scope.channel_id, scope.environment, body.client_id])
        mcp = await self.mcp.get(context, "mcp_connections", body.connection_id)
        snapshot = await self.mcp.get(context, "mcp_discoveries", body.discovery_id)
        remote = next(
            (t for t in snapshot["tool_definitions"] if t["name"] == body.remote_tool_name), None
        )
        if remote is None:
            raise ServiceError("SUBJECT_REVIEW_CHANGED", "身份工具不在所选快照中", 409)
        values = {
            **body.model_dump(exclude={"revision"}),
            "connection_revision": mcp["configuration_revision"],
            "schema_hash": remote["schema_hash"],
            "tool_name": remote.get("title") or "主体权限复核",
        }
        if body.enabled:
            await self.source(context, values)
        audit_id = new_id("audit")
        async with transaction(
            self.engine,
            scope,
            [
                configuration_key(scope),
                policy_key(scope.channel_id),
                policy_key("system"),
                record_key(scope.channel_id, "subject_review_bindings", binding_id),
                record_key(scope.channel_id, "mcp_connections", body.connection_id),
                record_key(scope.channel_id, "audit_events", audit_id),
            ],
        ) as uow:
            await require_management(uow, context, "integration:manage")
            await require_management(uow, context, "mcp:manage")
            client = await required(
                uow.connection,
                "service_clients",
                scope.channel_id,
                id=body.client_id,
                environment=scope.environment,
            )
            if client["status"] != "ACTIVE":
                raise ServiceError("FORBIDDEN", "接入服务未获当前环境授权", 403)
            current = await mcp_repository(scope, "mcp_connections").get(
                uow.connection, body.connection_id
            )
            if current != mcp:
                raise ServiceError("REVISION_CONFLICT", "连接配置已变化，请刷新", 409)
            repo = repository(scope, "subject_review_bindings")
            old = await repo.get(uow.connection, binding_id)
            if old:
                if body.revision is None:
                    raise ServiceError("REVISION_CONFLICT", "主体复核已配置，请刷新后编辑", 409)
                row = await repo.change(uow, binding_id, body.revision, values)
            else:
                if body.revision is not None:
                    raise ServiceError("REVISION_CONFLICT", "主体复核配置不存在", 409)
                row = await repo.add(uow, binding_id, values)
            await append_audit(
                uow,
                context,
                audit_id,
                "subject_review.configure",
                "subject_review",
                binding_id,
                {"state": "ACTIVE" if body.enabled else "DISABLED"},
            )
        return await self.view(context, row)

    async def read_current(
        self, context: AuthContext, claims: DelegationClaims
    ) -> SubjectAuthority:
        binding = await self.binding(context)
        row, remote = await self.source(context, binding)
        key = await self.mcp.key(context, row)
        if not context.client_id or not context.key_id:
            raise ServiceError("SUBJECT_REVIEW_REQUIRED", "主体复核缺少已验证接入身份", 403)
        payload = SubjectReviewRequest(
            scope=context.scope,
            client_id=context.client_id,
            key_id=context.key_id,
            request_id=context.request_id,
            actions=claims.actions,
            resources=claims.resources,
        ).model_dump(mode="json")
        validate_json(payload, remote.input_schema, "SUBJECT_REVIEW_INVALID")

        async def guard() -> None:
            # 此处只复核平台接入身份与固定配置；不能调用依赖 subject 的授权入口。
            await self.authorization.authentication.revalidate(context)
            if await self.binding(context) != binding:
                raise ServiceError("SUBJECT_REVIEW_CHANGED", "主体复核配置已变化", 403)
            current, _ = await self.source(context, binding)
            if current["configuration_revision"] != row["configuration_revision"]:
                raise ServiceError("SUBJECT_REVIEW_CHANGED", "主体复核连接已变化", 403)

        class BoundAuthorization:
            async def require(self, current: AuthContext, action: str, ref: str) -> None:
                if current != context or action != "credential:use" or ref != row["credential_ref"]:
                    raise ServiceError("FORBIDDEN", "凭据不属于固定身份复核连接", 403)
                await guard()

        async def invoke(secret: SecretBytes) -> SubjectAuthority:
            result = await self.mcp.transport.call(
                context.scope,
                key,
                row["endpoint"],
                secret.get_secret_value().decode(),
                McpTimeouts(
                    connect_seconds=min(5, binding["timeout_seconds"]),
                    operation_seconds=binding["timeout_seconds"],
                ),
                remote.name,
                remote.schema_hash,
                payload,
                65536,
                guard,
            )
            if result.isError or result.structuredContent is None:
                raise ServiceError("SUBJECT_REVIEW_INVALID", "源服务未返回有效主体权限", 403)
            try:
                current = SubjectReviewResponse.model_validate(result.structuredContent)
            except ValidationError:
                raise ServiceError(
                    "SUBJECT_REVIEW_INVALID", "主体复核响应不符合协议", 403
                ) from None
            now = utcnow()
            if (
                not current.active
                or current.scope != context.scope
                or not -5 <= (now - current.observed_at).total_seconds() <= 30
                or not 0 < (current.expires_at - now).total_seconds() <= 60
                or current.expires_at <= current.observed_at
            ):
                raise ServiceError("SUBJECT_REVIEW_DENIED", "主体停用、范围不符或复核已失效", 403)
            await guard()
            return SubjectAuthority(
                **current.model_dump(exclude={"protocol", "active", "observed_at"})
            )

        credentials = CredentialService(
            self.engine, self.mcp.credentials.keys, BoundAuthorization()
        )
        try:
            return await credentials.call(context, row["credential_ref"], "mcp", invoke)
        except ServiceError as exc:
            if exc.code.startswith(("MCP_", "CREDENTIAL_")):
                raise ServiceError(
                    "SUBJECT_REVIEW_UNAVAILABLE", "当前主体权限复核失败", 503
                ) from None
            raise
