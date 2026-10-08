"""连接管理、不可变发现与本地导入契约。"""

from typing import Any, Literal

from pydantic import AwareDatetime, Field, SecretStr

from creativity_service.core.contracts import DisplayStatus, VisibleAction
from creativity_service.core.primitives import Contract, Identifier, Revision
from creativity_service.modules.tools.schemas import EffectType, ToolImpact


class McpTimeouts(Contract):
    connect_seconds: int = Field(default=10, ge=1, le=30, strict=True)
    operation_seconds: int = Field(default=30, ge=1, le=120, strict=True)


class McpHealthPolicy(Contract):
    # 兼容既有配置与历史快照；检查间隔不再触发后台任务。
    interval_seconds: int = Field(default=300, ge=30, le=86400, strict=True)
    failure_threshold: int = Field(default=3, ge=1, le=10, strict=True)


class McpCreate(Contract):
    name: str = Field(min_length=1, max_length=128)
    transport: Literal["streamable_http", "stdio", "oauth"] = "streamable_http"
    endpoint: str = Field(min_length=1, max_length=2048)
    credential_ref: Identifier | None = None
    timeouts: McpTimeouts = McpTimeouts()
    health_policy: McpHealthPolicy = McpHealthPolicy()


class McpEdit(McpCreate):
    revision: Revision


class McpRevision(Contract):
    revision: Revision


class McpCredential(Contract):
    revision: Revision
    token: SecretStr = Field(min_length=1, max_length=8192)


class McpAuthentication(Contract):
    mode: Literal["bearer", "client_credentials"] = "bearer"
    token_endpoint: str | None = Field(default=None, max_length=2048)
    app_id: str | None = Field(default=None, max_length=128)


class McpAuthenticationInput(Contract):
    revision: Revision
    token_endpoint: str = Field(min_length=1, max_length=2048)
    app_id: str = Field(min_length=1, max_length=128, pattern=r"^[A-Za-z0-9._-]+$")
    app_secret: SecretStr | None = Field(default=None, min_length=16, max_length=4096)


class McpConnection(Contract):
    connection_id: Identifier
    name: str
    endpoint: str
    transport: str
    transport_label: str
    revision: Revision
    configuration_revision: Revision
    credential_mask: str | None
    authentication: McpAuthentication = McpAuthentication()
    timeouts: McpTimeouts
    health_policy: McpHealthPolicy
    status: DisplayStatus
    health: DisplayStatus
    last_check_at: AwareDatetime | None
    actions: list[VisibleAction]


class McpList(Contract):
    items: list[McpConnection]
    actions: list[VisibleAction]


class McpCheck(Contract):
    check_id: Identifier
    connection_revision: Revision
    operation: str
    negotiated_version: str | None
    server_info: dict[str, Any]
    capabilities: dict[str, Any]
    health: DisplayStatus
    latency_ms: int | None
    error_category: str | None
    error_message: str | None
    checked_at: AwareDatetime


class RemoteTool(Contract):
    name: str = Field(min_length=1, max_length=256)
    title: str | None = Field(default=None, max_length=128)
    description: str = Field(default="", max_length=16000)
    input_schema: dict[str, Any]
    output_schema: dict[str, Any] | None = None
    annotations: dict[str, Any] = Field(default_factory=dict)
    schema_hash: str
    purpose: Literal["business", "subject_review"] = "business"


class McpDiscovery(Contract):
    discovery_id: Identifier
    connection_revision: Revision
    negotiated_version: str
    tools: list[RemoteTool]
    discovered_at: AwareDatetime


class McpDifference(Contract):
    remote_tool_name: str
    name: str | None
    changes: list[str]
    labels: list[str]
    breaking: bool


class McpDiff(Contract):
    discovery_id: Identifier
    previous_discovery_id: Identifier | None
    items: list[McpDifference]


class McpImportInput(Contract):
    discovery_id: Identifier
    target_tool_id: Identifier | None = None
    remote_tool_name: str = Field(min_length=1, max_length=256)
    name: str = Field(min_length=1, max_length=128)
    description: str = Field(min_length=1, max_length=4000)
    owner: str = Field(min_length=1, max_length=128)
    version_label: str = Field(min_length=1, max_length=64)
    effect_type: EffectType
    required_scopes: tuple[str, ...] = Field(min_length=1)
    timeout_seconds: int = Field(ge=1, le=120, strict=True)
    max_result_size: int = Field(ge=256, le=2097152, strict=True)
    output_schema: dict[str, Any]
    subject_required: bool = True


class McpImport(Contract):
    import_id: Identifier
    discovery_id: Identifier
    remote_tool_name: str
    name: str
    local_tool_id: Identifier
    imported_version: Identifier
    schema_hash: str
    available: bool
    unavailable_reason: str | None


class McpImpact(Contract):
    tools: list[ToolImpact]
    ongoing_calls: int
    message: str


class McpDetail(Contract):
    connection: McpConnection
    checks: list[McpCheck]
    discoveries: list[McpDiscovery]
    imports: list[McpImport]
