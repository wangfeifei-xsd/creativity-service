"""连接、握手、发现、差异和导入服务；外部调用始终位于短事务之外。"""

from datetime import timedelta
from time import monotonic
from typing import Any
from urllib.parse import urlsplit

from pydantic import SecretBytes
from sqlalchemy.ext.asyncio import AsyncEngine

from creativity_service.core.context import AuthContext
from creativity_service.core.contracts import DisplayStatus, VisibleAction
from creativity_service.core.database import Repository, transaction
from creativity_service.core.database.tables import metadata as core_metadata
from creativity_service.core.locking import ResourceKey, record_key
from creativity_service.core.observability.audit import append_audit
from creativity_service.core.primitives import ServiceError, digest, new_id, utcnow
from creativity_service.core.security.credentials import CredentialService
from creativity_service.core.security.outbound import OutboundPolicy
from creativity_service.modules.iam.authorization import IamAuthorization
from creativity_service.modules.iam.roles import ACTION_NAMES
from creativity_service.modules.mcp.bindings import require_current_binding
from creativity_service.modules.mcp.differences import differences
from creativity_service.modules.mcp.repositories import repository
from creativity_service.modules.mcp.schemas import (
    McpCheck,
    McpConnection,
    McpCreate,
    McpCredential,
    McpDetail,
    McpDiff,
    McpDiscovery,
    McpEdit,
    McpImpact,
    McpImport,
    McpImportInput,
    McpList,
    McpTimeouts,
    RemoteTool,
)
from creativity_service.modules.mcp.transport import DiscoveryResult, McpTransport, SessionKey
from creativity_service.modules.tools.schemas import (
    BindingOption,
    ToolBinding,
    ToolCreate,
    ToolDefinition,
    ToolVersionCreate,
)
from creativity_service.modules.tools.services import ToolService

ERRORS = {
    "MCP_AUTH_FAILED": "远端鉴权失败，请更新凭据",
    "MCP_PROTOCOL_MISMATCH": "远端协议或工具能力不兼容",
    "MCP_TOOL_CHANGED": "远端契约已变更，请导入新版本并重新验证",
    "MCP_UNAVAILABLE": "远端连接失败或响应超时",
    "MCP_RESULT_UNKNOWN": "远端调用已提交，结果待核实",
    "MCP_RESULT_TOO_LARGE": "远端响应超过体积上限",
    "MCP_RESULT_INVALID": "远端响应不符合契约",
    "MCP_DESTINATION_FORBIDDEN": "远端地址或重定向未获授权",
}


def display(value: str) -> DisplayStatus:
    labels = {
        "ENABLED": "已启用",
        "DISABLED": "未启用",
        "HEALTHY": "正常",
        "DEGRADED": "异常",
        "UNAVAILABLE": "不可用",
        "UNKNOWN": "未知",
    }
    return DisplayStatus(
        value=value,
        label=labels[value],
        tone="success"
        if value in {"ENABLED", "HEALTHY"}
        else "error"
        if value == "UNAVAILABLE"
        else "warning"
        if value == "DEGRADED"
        else "default",
    )


def discovery_view(row: dict[str, Any]) -> McpDiscovery:
    return McpDiscovery(
        discovery_id=row["id"],
        connection_revision=row["connection_revision"],
        negotiated_version=row["negotiated_version"],
        tools=row["tool_definitions"],
        discovered_at=row["created_at"],
    )


def check_view(row: dict[str, Any]) -> McpCheck:
    return McpCheck(
        check_id=row["id"],
        connection_revision=row["connection_revision"],
        operation=row["operation"],
        negotiated_version=row["negotiated_version"],
        server_info=row["server_info"],
        capabilities=row["capabilities"],
        health=display(row["health_status"]),
        latency_ms=row["latency_ms"],
        error_category=row["error_category"],
        error_message=ERRORS.get(row["error_category"]),
        checked_at=row["created_at"],
    )


class McpService:
    def __init__(
        self,
        engine: AsyncEngine,
        authorization: IamAuthorization,
        tools: ToolService,
        credentials: CredentialService,
        outbound: OutboundPolicy,
        transport: McpTransport | None = None,
    ) -> None:
        self.engine, self.authorization, self.tools = engine, authorization, tools
        self.credentials, self.outbound = credentials, outbound
        self.transport = transport or McpTransport(outbound)
        from creativity_service.modules.mcp.oauth import OAuthService

        self.oauth = OAuthService(self)

    async def require(self, context: AuthContext, connection_id: str) -> None:
        if context.principal_type not in {"management", "worker"} or not context.actor_id:
            raise ServiceError("FORBIDDEN", "连接管理仅面向管理成员", 403)
        await self.authorization.boundary(context, "mcp:manage", "mcp_connection", connection_id)

    async def get(self, context: AuthContext, table: str, record_id: str) -> dict[str, Any]:
        async with self.engine.connect() as conn:
            row = await repository(context.scope, table).get(conn, record_id)
        if row is None:
            raise ServiceError("NOT_FOUND", "连接记录不存在", 404)
        return row

    async def rows(self, context: AuthContext, table: str, **filters: Any) -> list[dict[str, Any]]:
        async with self.engine.connect() as conn:
            rows = await repository(context.scope, table).find(conn, **filters)
        return sorted(rows, key=lambda r: (r["created_at"], r["id"]), reverse=True)

    async def credential_row(self, context: AuthContext, ref: str | None) -> dict[str, Any] | None:
        if ref is None:
            return None
        async with self.engine.connect() as conn:
            row = await Repository(core_metadata.tables["credentials"], context.scope).get(
                conn, ref
            )
        if row is None or row["purpose"] != "mcp" or row["state"] != "ACTIVE":
            raise ServiceError("MCP_AUTH_FAILED", "连接凭据不可用", 403)
        return row

    async def validate_config(self, context: AuthContext, body: McpCreate) -> int | None:
        if not context.scope.data_scope_id:
            raise ServiceError("CONTEXT_REQUIRED", "请先选择已授权业务数据域", 403)
        if body.transport == "stdio":
            from creativity_service.integrations.sandbox import ContainerSandbox
            from creativity_service.modules.mcp.stdio import profile_for

            profile_for(ContainerSandbox(), context.scope, body.endpoint)
        if body.transport == "oauth" and body.credential_ref:
            raise ServiceError("OAUTH_PROFILE_INVALID", "OAuth 连接通过独立授权流程保存凭据", 422)
        if urlsplit(body.endpoint).query:
            raise ServiceError(
                "MCP_DESTINATION_FORBIDDEN", "服务地址不能包含查询参数，凭据请单独配置", 422
            )
        if body.transport != "stdio":
            await self.outbound.validate(context.scope, "mcp", body.endpoint)
        row = await self.credential_row(context, body.credential_ref)
        if row:
            await self.authorization.boundary(context, "credential:use", "credential", row["id"])
        return row["revision"] if row else None

    async def view(self, context: AuthContext, row: dict[str, Any]) -> McpConnection:
        permitted = (
            await self.authorization.check(context, "mcp:manage", "mcp_connection", row["id"])
        ).allowed
        actions = []
        if permitted:
            actions = [
                VisibleAction(action_key=k, label=v)
                for k, v in [
                    ("edit", "编辑"),
                    ("credential", "更新凭据"),
                    ("test", "连接测试"),
                    ("discover", "发现工具"),
                    ("disable", "停用") if row["status"] == "ENABLED" else ("enable", "启用"),
                ]
            ]
            if all(
                [
                    (await self.authorization.check(context, action, "tool", "new")).allowed
                    for action in ("tool:manage", "version:edit")
                ]
            ):
                actions.append(VisibleAction(action_key="import", label="导入草稿"))
        return McpConnection(
            connection_id=row["id"],
            name=row["name"],
            endpoint=row["endpoint"],
            transport=row["transport"],
            transport_label={
                "streamable_http": "Streamable HTTP",
                "stdio": "隔离 stdio",
                "oauth": "OAuth 委托",
            }[row["transport"]],
            revision=row["revision"],
            configuration_revision=row["configuration_revision"],
            credential_mask="••••••••" if row["credential_ref"] else None,
            timeouts=row["timeouts"],
            health_policy=row["health_policy"],
            status=display(row["status"]),
            health=display(row["health_status"]),
            last_check_at=row["last_check_at"],
            actions=actions,
        )

    async def list_connections(self, context: AuthContext) -> McpList:
        await self.authorization.authentication.revalidate(context)
        items = []
        for row in await self.rows(context, "mcp_connections"):
            if (
                await self.authorization.check(context, "mcp:manage", "mcp_connection", row["id"])
            ).allowed:
                items.append(await self.view(context, row))
        allowed = (
            await self.authorization.check(context, "mcp:manage", "mcp_connection", "new")
        ).allowed
        return McpList(
            items=items,
            actions=[VisibleAction(action_key="create", label="新增连接")] if allowed else [],
        )

    async def create(self, context: AuthContext, body: McpCreate) -> McpConnection:
        await self.require(context, "new")
        credential_revision = await self.validate_config(context, body)
        connection_id, audit_id = new_id("mcp"), new_id("audit")
        scope = context.scope
        async with transaction(
            self.engine,
            scope,
            [
                record_key(scope.channel_id, "mcp_connections", connection_id),
                ResourceKey(scope.channel_id, "mcp-name", (scope.environment, body.name)),
                record_key(scope.channel_id, "audit_events", audit_id),
            ],
        ) as uow:
            repo = repository(scope, "mcp_connections")
            if await repo.find(uow.connection, name=body.name):
                raise ServiceError("CODE_EXISTS", "连接名称已存在", 409)
            row = await repo.add(
                uow,
                connection_id,
                {
                    **body.model_dump(mode="json"),
                    "status": "DISABLED",
                    "health_status": "UNKNOWN",
                    "configuration_revision": 1,
                    "credential_revision": credential_revision,
                    "tested_revision": None,
                    "discovered_revision": None,
                    "failure_count": 0,
                    "last_check_at": None,
                    "auth_failed": False,
                    "health_actor_id": context.actor_id,
                    "health_data_scope_id": context.scope.data_scope_id,
                    "next_check_at": utcnow()
                    + timedelta(seconds=body.health_policy.interval_seconds),
                },
            )
            await append_audit(
                uow, context, audit_id, "mcp.create", "mcp_connection", connection_id, {}
            )
        return await self.view(context, row)

    async def edit(self, context: AuthContext, connection_id: str, body: McpEdit) -> McpConnection:
        await self.require(context, connection_id)
        # 页面不回传密钥引用；省略引用时保留现有凭据，轮换使用专用接口。
        current = await self.get(context, "mcp_connections", connection_id)
        config = body.model_copy(
            update={
                "credential_ref": body.credential_ref
                if "credential_ref" in body.model_fields_set
                else current["credential_ref"]
            }
        )
        cred = await self.validate_config(context, config)
        values = {
            **config.model_dump(mode="json", exclude={"revision"}),
            "configuration_revision": current["configuration_revision"] + 1,
            "credential_revision": cred,
            "tested_revision": None,
            "discovered_revision": None,
            "status": "DISABLED",
            "health_status": "UNKNOWN",
            "failure_count": 0,
            "auth_failed": False,
        }
        return await self.change(context, connection_id, body.revision, values, "edit", body.name)

    async def change(
        self,
        context: AuthContext,
        connection_id: str,
        revision: int,
        values: dict[str, Any],
        action: str,
        name: str | None = None,
    ) -> McpConnection:
        scope, audit_id = context.scope, new_id("audit")
        keys = [
            record_key(scope.channel_id, "mcp_connections", connection_id),
            record_key(scope.channel_id, "audit_events", audit_id),
        ]
        if name:
            keys.append(ResourceKey(scope.channel_id, "mcp-name", (scope.environment, name)))
        async with transaction(self.engine, scope, keys) as uow:
            repo = repository(scope, "mcp_connections")
            if name and any(
                r["id"] != connection_id for r in await repo.find(uow.connection, name=name)
            ):
                raise ServiceError("CODE_EXISTS", "连接名称已存在", 409)
            row = await repo.change(uow, connection_id, revision, values)
            await append_audit(
                uow, context, audit_id, f"mcp.{action}", "mcp_connection", connection_id, {}
            )
        return await self.view(context, row)

    async def rotate(
        self, context: AuthContext, connection_id: str, body: McpCredential
    ) -> McpConnection:
        await self.require(context, connection_id)
        row = await self.get(context, "mcp_connections", connection_id)
        if row["revision"] != body.revision:
            raise ServiceError("REVISION_CONFLICT", "配置已变更，请刷新后更新凭据", 409)
        token = body.token.get_secret_value()
        if any(ord(c) < 33 or ord(c) > 126 for c in token):
            raise ServiceError("MCP_AUTH_FAILED", "服务令牌格式不正确", 422)
        ref = await self.credentials.store(context, "mcp", SecretBytes(token.encode()))
        # 新引用使旧会话永远不能被新调用选用；在途会话结束后立即关闭。
        return await self.change(
            context,
            connection_id,
            body.revision,
            {
                "credential_ref": ref,
                "credential_revision": 1,
                "configuration_revision": row["configuration_revision"] + 1,
                "tested_revision": None,
                "discovered_revision": None,
                "status": "DISABLED",
                "health_status": "UNKNOWN",
                "failure_count": 0,
                "auth_failed": False,
            },
            "credential",
        )

    async def set_enabled(
        self, context: AuthContext, connection_id: str, revision: int, enabled: bool
    ) -> McpConnection:
        await self.require(context, connection_id)
        scope = context.scope
        audit_id = new_id("audit")
        async with transaction(
            self.engine,
            scope,
            [
                record_key(scope.channel_id, "mcp_connections", connection_id),
                record_key(scope.channel_id, "audit_events", audit_id),
            ],
        ) as uow:
            repo = repository(scope, "mcp_connections")
            row = await repo.get(uow.connection, connection_id)
            if row is None:
                raise ServiceError("NOT_FOUND", "连接不存在", 404)
            if enabled:
                credential = (
                    await Repository(core_metadata.tables["credentials"], scope).get(
                        uow.connection, row["credential_ref"]
                    )
                    if row["credential_ref"]
                    else None
                )
                if (
                    row["tested_revision"] != row["configuration_revision"]
                    or row["discovered_revision"] != row["configuration_revision"]
                    or row["auth_failed"]
                    or row["health_status"] != "HEALTHY"
                    or (
                        row["credential_ref"]
                        and (
                            not credential
                            or credential["state"] != "ACTIVE"
                            or credential["revision"] != row["credential_revision"]
                        )
                    )
                ):
                    raise ServiceError(
                        "MCP_TEST_REQUIRED", "当前配置须完成握手与发现测试后才能启用", 409
                    )
            changed = await repo.change(
                uow, connection_id, revision, {"status": "ENABLED" if enabled else "DISABLED"}
            )
            await append_audit(
                uow,
                context,
                audit_id,
                "mcp.enable" if enabled else "mcp.disable",
                "mcp_connection",
                connection_id,
                {},
            )
        return await self.view(context, changed)

    async def key(self, context: AuthContext, row: dict[str, Any]) -> SessionKey:
        if row["transport"] == "oauth":
            _, grant = await self.oauth.grant(context, row["id"])
            return SessionKey(
                context.scope.channel_id,
                context.scope.environment,
                row["id"],
                row["configuration_revision"],
                grant["credential_ref"],
                grant["revision"],
            )
        credential = await self.credential_row(context, row["credential_ref"])
        if credential and credential["revision"] != row["credential_revision"]:
            raise ServiceError("MCP_AUTH_FAILED", "凭据版本已变化，请更新配置并重新测试", 403)
        return SessionKey(
            context.scope.channel_id,
            context.scope.environment,
            row["id"],
            row["configuration_revision"],
            row["credential_ref"],
            row["credential_revision"],
        )

    async def probe(
        self, context: AuthContext, connection_id: str, discover: bool
    ) -> McpCheck | McpDiscovery:
        await self.require(context, connection_id)
        row = await self.get(context, "mcp_connections", connection_id)
        if row["auth_failed"]:
            raise ServiceError("MCP_AUTH_FAILED", "凭据已失效，请更新后重试", 409)
        started = monotonic()
        result = None
        error = None
        try:
            key = await self.key(context, row)

            async def operation(secret: SecretBytes | None) -> DiscoveryResult:
                return await self.transport.discover(
                    context.scope,
                    key,
                    row["endpoint"],
                    secret.get_secret_value().decode() if secret else None,
                    McpTimeouts.model_validate(row["timeouts"]),
                )

            result = (
                await self.oauth.call(context, row, operation)
                if row["transport"] == "oauth"
                else await self.credentials.call(context, row["credential_ref"], "mcp", operation)
                if row["credential_ref"]
                else await operation(None)
            )
        except ServiceError as exc:
            error = (
                exc
                if exc.code.startswith("MCP_")
                else ServiceError("MCP_UNAVAILABLE", "连接检查失败", 502)
            )
        await self.require(context, connection_id)
        check, snapshot = await self.record_probe(
            context, row, result, error, int((monotonic() - started) * 1000), discover
        )
        return discovery_view(snapshot) if snapshot else check_view(check)

    async def record_probe(
        self,
        context: AuthContext,
        initial: dict[str, Any],
        result: DiscoveryResult | None,
        error: ServiceError | None,
        latency: int,
        discover: bool,
    ) -> tuple[dict[str, Any], dict[str, Any] | None]:
        scope = context.scope
        check_id, snapshot_id = new_id("mcp_check"), new_id("discovery")
        async with transaction(
            self.engine,
            scope,
            [
                record_key(scope.channel_id, "mcp_connections", initial["id"]),
                record_key(scope.channel_id, "mcp_checks", check_id),
                record_key(scope.channel_id, "mcp_discoveries", snapshot_id),
            ],
        ) as uow:
            repo = repository(scope, "mcp_connections")
            row = await repo.get(uow.connection, initial["id"])
            if (
                row is None
                or row["configuration_revision"] != initial["configuration_revision"]
                or row["last_check_at"] != initial["last_check_at"]
            ):
                raise ServiceError("REVISION_CONFLICT", "测试期间连接配置已变化，请重新测试", 409)
            count = row["failure_count"] + 1 if error else 0
            health = (
                "UNAVAILABLE"
                if error
                and (
                    error.code == "MCP_AUTH_FAILED"
                    or count >= row["health_policy"]["failure_threshold"]
                )
                else "DEGRADED"
                if error
                else "HEALTHY"
            )
            check = await repository(scope, "mcp_checks").add(
                uow,
                check_id,
                {
                    "connection_id": row["id"],
                    "connection_revision": row["configuration_revision"],
                    "operation": "discover" if discover else "test",
                    "negotiated_version": result.handshake.protocolVersion if result else None,
                    "server_info": result.handshake.serverInfo.model_dump(
                        mode="json", exclude_none=True
                    )
                    if result
                    else {},
                    "capabilities": result.handshake.capabilities.model_dump(
                        mode="json", exclude_none=True
                    )
                    if result
                    else {},
                    "health_status": health,
                    "latency_ms": latency,
                    "error_category": error.code if error else None,
                },
            )
            snapshot = None
            if result and discover:
                snapshot = await repository(scope, "mcp_discoveries").add(
                    uow,
                    snapshot_id,
                    {
                        "connection_id": row["id"],
                        "connection_revision": row["configuration_revision"],
                        "negotiated_version": result.handshake.protocolVersion,
                        "credential_revision": row["credential_revision"],
                        "tool_definitions": [t.model_dump(mode="json") for t in result.tools],
                        "schema_hashes": {t.name: t.schema_hash for t in result.tools},
                    },
                )
            values = {
                "failure_count": count,
                "health_status": health,
                "last_check_at": utcnow(),
                "auth_failed": bool(error and error.code == "MCP_AUTH_FAILED"),
                "next_check_at": utcnow()
                + timedelta(seconds=row["health_policy"]["interval_seconds"]),
                "health_actor_id": context.actor_id,
                "health_data_scope_id": context.scope.data_scope_id,
            }
            if result:
                values["tested_revision"] = row["configuration_revision"]
                if discover:
                    values["discovered_revision"] = row["configuration_revision"]
            await repo.change(uow, row["id"], row["revision"], values)
        return check, snapshot

    async def import_view(self, context: AuthContext, row: dict[str, Any]) -> McpImport:
        reason = None
        try:
            await self.binding_state(
                context,
                ToolBinding(
                    adapter_key=row["id"],
                    implementation_version=row["schema_hash"],
                    connection_id=row["connection_id"],
                ),
            )
        except ServiceError as exc:
            reason = exc.message
        return McpImport(
            import_id=row["id"],
            discovery_id=row["discovery_id"],
            remote_tool_name=row["remote_tool_name"],
            name=row["name"],
            local_tool_id=row["local_tool_id"],
            imported_version=row["imported_version"],
            schema_hash=row["schema_hash"],
            available=reason is None,
            unavailable_reason=reason,
        )

    async def detail(self, context: AuthContext, connection_id: str) -> McpDetail:
        await self.require(context, connection_id)
        row = await self.get(context, "mcp_connections", connection_id)
        return McpDetail(
            connection=await self.view(context, row),
            checks=[
                check_view(r)
                for r in (await self.rows(context, "mcp_checks", connection_id=connection_id))[:100]
            ],
            discoveries=[
                discovery_view(r)
                for r in (await self.rows(context, "mcp_discoveries", connection_id=connection_id))[
                    :20
                ]
            ],
            imports=[
                await self.import_view(context, r)
                for r in await self.rows(context, "mcp_imports", connection_id=connection_id)
            ],
        )

    async def diff(self, context: AuthContext, connection_id: str, discovery_id: str) -> McpDiff:
        await self.require(context, connection_id)
        rows = await self.rows(context, "mcp_discoveries", connection_id=connection_id)
        index = next((i for i, r in enumerate(rows) if r["id"] == discovery_id), None)
        if index is None:
            raise ServiceError("NOT_FOUND", "发现快照不存在", 404)
        return differences(
            discovery_view(rows[index]),
            discovery_view(rows[index + 1]) if index + 1 < len(rows) else None,
        )

    async def import_tool(
        self, context: AuthContext, connection_id: str, body: McpImportInput
    ) -> McpImport:
        await self.require(context, connection_id)
        await self.tools.require(context, "tool:manage", body.target_tool_id or "new")
        await self.tools.require(context, "version:edit", body.target_tool_id or "new")
        snapshot = await self.get(context, "mcp_discoveries", body.discovery_id)
        if snapshot["connection_id"] != connection_id:
            raise ServiceError("NOT_FOUND", "发现快照不存在", 404)
        remote = next(
            (
                RemoteTool.model_validate(t)
                for t in snapshot["tool_definitions"]
                if t["name"] == body.remote_tool_name
            ),
            None,
        )
        if remote is None:
            raise ServiceError("NOT_FOUND", "远端工具不存在", 404)
        if remote.purpose == "subject_review":
            raise ServiceError("MCP_IMPORT_INVALID", "身份复核工具仅能用于受控主体复核配置", 422)
        if any(s not in ACTION_NAMES for s in body.required_scopes):
            raise ServiceError("MCP_IMPORT_INVALID", "业务权限不在平台授权目录中", 422)
        scope = context.scope
        effect = {"READ_ONLY": "ro", "IDEMPOTENT_WRITE": "iw", "EXTERNAL_WRITE": "ew"}[
            body.effect_type
        ]
        identity = digest(
            [scope.channel_id, scope.environment, connection_id, body.discovery_id, remote.name]
        )[:52]
        import_id = f"mcp_{effect}_{identity}"
        # 重复请求无论修改何种表单字段，均返回首次已提交的导入映射。
        existing = await self.rows(
            context,
            "mcp_imports",
            connection_id=connection_id,
            discovery_id=body.discovery_id,
            remote_tool_name=remote.name,
        )
        if existing:
            return await self.import_view(context, existing[0])
        if body.target_tool_id:
            targets = await self.rows(context, "mcp_imports", local_tool_id=body.target_tool_id)
            if not targets or any(
                t["connection_id"] != connection_id or t["remote_tool_name"] != remote.name
                for t in targets
            ):
                raise ServiceError("MCP_IMPORT_INVALID", "目标工具必须来自同一连接和远端工具", 422)
        schema = {**remote.input_schema, "additionalProperties": False}
        if body.effect_type == "READ_ONLY" and (
            remote.annotations.get("readOnlyHint") is not True
            or remote.annotations.get("destructiveHint") is True
        ):
            raise ServiceError(
                "MCP_EFFECT_UNCONFIRMED", "远端未声明只读，请按真实影响登记草稿", 422
            )
        definition = ToolDefinition(
            input_schema=schema,
            output_schema=body.output_schema,
            model_fields_allowed=tuple(schema.get("properties", {})),
            binding=ToolBinding(
                adapter_key=import_id,
                implementation_version=remote.schema_hash,
                connection_id=connection_id,
            ),
            effect_type=body.effect_type,
            required_scopes=body.required_scopes,
            allowed_data_domains=(scope.data_scope_id,) if scope.data_scope_id else (),
            environments=(scope.environment,),
            subject_requirements={"required": body.subject_required},
            timeout_seconds=body.timeout_seconds,
            max_result_size=body.max_result_size,
        )
        await self.tools.validate_config(context, definition)
        code = "mcp." + identity
        tool = ToolCreate(
            tool_code=code,
            name=body.name,
            description=body.description,
            owner=body.owner,
            source_type="mcp",
        )
        keys = self.tools.import_keys(
            context, import_id, code, body.target_tool_id, body.version_label
        ) + [
            record_key(scope.channel_id, "mcp_connections", connection_id),
            ResourceKey(
                scope.channel_id,
                "mcp-import",
                (scope.environment, connection_id, body.discovery_id, remote.name),
            ),
            record_key(scope.channel_id, "mcp_imports", import_id),
        ]
        async with transaction(self.engine, scope, keys) as uow:
            repo = repository(scope, "mcp_imports")
            duplicates = await repo.find(
                uow.connection,
                connection_id=connection_id,
                discovery_id=body.discovery_id,
                remote_tool_name=remote.name,
            )
            if duplicates:
                row = duplicates[0]
            else:
                connection = await repository(scope, "mcp_connections").get(
                    uow.connection, connection_id
                )
                if (
                    connection is None
                    or snapshot["connection_revision"] != connection["configuration_revision"]
                ):
                    raise ServiceError("MCP_TOOL_CHANGED", "连接已变更，请重新发现工具", 409)
                latest = await repository(scope, "mcp_discoveries").find(
                    uow.connection, connection_id=connection_id
                )
                newest = max(latest, key=lambda r: (r["created_at"], r["id"]))
                if newest["schema_hashes"].get(remote.name) != remote.schema_hash:
                    raise ServiceError("MCP_TOOL_CHANGED", "所选快照的工具契约已过期", 409)
                version = await self.tools.import_draft(
                    context,
                    tool,
                    ToolVersionCreate(version_label=body.version_label, definition=definition),
                    uow=uow,
                    import_key=import_id,
                    target_tool_id=body.target_tool_id,
                )
                row = await repo.add(
                    uow,
                    import_id,
                    {
                        "connection_id": connection_id,
                        "discovery_id": body.discovery_id,
                        "remote_tool_name": remote.name,
                        "local_tool_id": version.version.resource_id,
                        "schema_hash": remote.schema_hash,
                        "imported_version": version.version.version_id,
                        "effect_type": body.effect_type,
                        "name": body.name,
                        "input_schema": schema,
                        "contract_status": "CURRENT",
                    },
                )
        return await self.import_view(context, row)

    async def binding_state(
        self, context: AuthContext, binding: ToolBinding
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        imported = await self.get(context, "mcp_imports", binding.adapter_key)
        row = await self.get(context, "mcp_connections", imported["connection_id"])
        original = await self.get(context, "mcp_discoveries", imported["discovery_id"])
        discoveries = await self.rows(context, "mcp_discoveries", connection_id=row["id"])
        require_current_binding(
            binding, row, imported, original, discoveries[0] if discoveries else None
        )
        await self.key(context, row)
        return row, imported

    async def check_binding(self, context: AuthContext, definition: ToolDefinition) -> None:
        if not definition.binding.adapter_key.startswith("mcp_"):
            return
        _, imported = await self.binding_state(context, definition.binding)
        if (
            definition.input_schema != imported["input_schema"]
            or definition.effect_type != imported["effect_type"]
            or definition.cache_policy.ttl_seconds
        ):
            raise ServiceError(
                "MCP_TOOL_CHANGED", "MCP 固定输入和影响类型不能改写，当前不启用结果缓存", 409
            )

    async def invalidate_binding(self, context: AuthContext, import_id: str) -> None:
        scope = context.scope
        async with transaction(
            self.engine, scope, [record_key(scope.channel_id, "mcp_imports", import_id)]
        ) as uow:
            repo = repository(scope, "mcp_imports")
            row = await repo.get(uow.connection, import_id)
            if row and row["contract_status"] == "CURRENT":
                await repo.change(uow, import_id, row["revision"], {"contract_status": "CHANGED"})

    async def impact(self, context: AuthContext, connection_id: str) -> McpImpact:
        await self.require(context, connection_id)
        mappings = await self.rows(context, "mcp_imports", connection_id=connection_id)
        impacts = [
            await self.tools.impact(context, tool_id)
            for tool_id in dict.fromkeys(m["local_tool_id"] for m in mappings)
        ]
        return McpImpact(
            tools=impacts,
            ongoing_calls=sum(t.ongoing_calls for t in impacts),
            message="停用后阻止新调用；在途调用保留已取得的结果或结果待核实记录。",
        )

    async def bindings(self, context: AuthContext) -> list[BindingOption]:
        result = []
        labels = {"READ_ONLY": "只读", "IDEMPOTENT_WRITE": "幂等写入", "EXTERNAL_WRITE": "外部写入"}
        for row in await self.rows(context, "mcp_imports"):
            if not (
                await self.authorization.check(context, "tool:manage", "tool", row["local_tool_id"])
            ).allowed:
                continue
            view = await self.import_view(context, row)
            reason = view.unavailable_reason or (
                "写入须配置核查工具并经逐次审批" if row["effect_type"] != "READ_ONLY" else None
            )
            result.append(
                BindingOption(
                    binding=ToolBinding(
                        adapter_key=row["id"],
                        implementation_version=row["schema_hash"],
                        connection_id=row["connection_id"],
                    ),
                    name=row["name"],
                    source_type="mcp",
                    effect_type=row["effect_type"],
                    effect_label=labels[row["effect_type"]],
                    execution_enabled=reason is None,
                    unavailable_reason=reason,
                )
            )
        return result
