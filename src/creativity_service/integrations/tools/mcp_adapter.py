"""MCP 仅作为统一工具执行器的来源适配器，远端文本不参与授权。"""

from typing import TYPE_CHECKING, Any

from pydantic import SecretBytes

from creativity_service.core.context import AuthContext, Scope
from creativity_service.core.primitives import ServiceError
from creativity_service.core.security.credentials import CredentialService
from creativity_service.integrations.tools import (
    AdapterRegistration,
    AdapterRequest,
    AdapterResult,
    ToolAdapterError,
)
from creativity_service.modules.mcp.contracts import trusted_identity, wrap_result
from creativity_service.modules.mcp.schemas import McpTimeouts
from creativity_service.modules.tools.schemas import EffectType, ToolBinding

if TYPE_CHECKING:
    from creativity_service.modules.mcp.services import McpService


class McpAdapter:
    def __init__(self, service: "McpService") -> None:
        self.service = service

    async def invoke(self, request: AdapterRequest) -> AdapterResult:
        service, context = self.service, request.context
        await service.check_binding(context, request.definition)
        row, imported = await service.binding_state(context, request.definition.binding)
        if (
            request.definition.subject_requirements.required
            and not row["credential_ref"]
            and row["transport"] != "oauth"
        ):
            raise ToolAdapterError("MCP_AUTH_FAILED", "业务主体传递需要受控服务凭据")
        if imported["effect_type"] != "READ_ONLY" and not request.operation:
            raise ToolAdapterError("TOOL_CONFIRMATION_REQUIRED", "写入工具缺少已确认的执行意图")
        key = await service.key(context, row)

        class BoundCredentialAuthorization:
            async def require(self, current: AuthContext, action: str, resource_id: str) -> None:
                # 执行权限仅允许使用本次固定工具绑定的服务凭据，不授予凭据管理权限。
                if (
                    current != context
                    or action != "credential:use"
                    or resource_id != row["credential_ref"]
                ):
                    raise ServiceError("FORBIDDEN", "凭据不属于本次工具绑定", 403)
                await service.authorization.boundary(
                    current, "run:create", "tool", imported["local_tool_id"]
                )

        bound_credentials = CredentialService(
            service.engine, authorization=BoundCredentialAuthorization()
        )

        identity: dict[str, Any] = {}

        async def before_send() -> None:
            await service.authorization.boundary(
                context, "run:create", "tool", imported["local_tool_id"]
            )
            identity.clear()
            identity.update(
                await trusted_identity(
                    service.authorization, context, request, imported["local_tool_id"]
                )
            )
            current, _ = await service.binding_state(context, request.definition.binding)
            if current["configuration_revision"] != row["configuration_revision"]:
                raise ServiceError("MCP_AUTH_FAILED", "连接或凭据已轮换，请重新发起调用", 409)

        async def operation(secret: SecretBytes | None) -> AdapterResult:
            result = await service.transport.call(
                context.scope,
                key,
                row["endpoint"],
                secret.get_secret_value().decode() if secret else None,
                McpTimeouts.model_validate(row["timeouts"]).model_copy(
                    update={
                        "operation_seconds": min(
                            row["timeouts"]["operation_seconds"], request.definition.timeout_seconds
                        )
                    }
                ),
                imported["remote_tool_name"],
                imported["schema_hash"],
                request.arguments,
                request.definition.max_result_size,
                before_send,
                identity,
            )
            return wrap_result(result, request, imported["schema_hash"])

        try:
            if row["transport"] == "oauth":
                return await service.oauth.call(context, row, operation)

            async def authenticated(secret: SecretBytes) -> AdapterResult:
                if (row.get("authentication") or {}).get("mode") == "client_credentials":
                    return await service.client_credentials.call(context, row, secret, operation)
                return await operation(secret)

            return (
                await bound_credentials.call(context, row["credential_ref"], "mcp", authenticated)
                if row["credential_ref"]
                else await operation(None)
            )
        except ServiceError as exc:
            if exc.code == "MCP_TOOL_CHANGED":
                await service.invalidate_binding(context, imported["id"])
            if exc.code == "MCP_AUTH_FAILED" and row["transport"] != "oauth":
                await service.record_probe(context, row, None, exc, 0, False)
            raise ToolAdapterError(
                exc.code,
                exc.message,
                retryable=exc.code == "MCP_UNAVAILABLE",
                source_request_id=getattr(exc, "source_request_id", None) or request.attempt_id,
            ) from None


def resolve_adapter(
    service: "McpService", scope: Scope, binding: ToolBinding
) -> AdapterRegistration | None:
    prefixes: dict[str, EffectType] = {
        "mcp_ro_": "READ_ONLY",
        "mcp_iw_": "IDEMPOTENT_WRITE",
        "mcp_ew_": "EXTERNAL_WRITE",
    }
    effect = next(
        (effect for prefix, effect in prefixes.items() if binding.adapter_key.startswith(prefix)),
        None,
    )
    if effect is None:
        return None
    return AdapterRegistration(
        key=binding.adapter_key,
        name="远程 MCP 工具",
        source_type="mcp",
        implementation_version=binding.implementation_version,
        actual_effect=effect,
        adapter=McpAdapter(service),
        channel_id=scope.channel_id,
        environment=scope.environment,
        connection_id=binding.connection_id,
        sensitive=False,
    )
