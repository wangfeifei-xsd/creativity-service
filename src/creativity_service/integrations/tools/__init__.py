"""受控工具适配端口和显式注册表，不执行上传代码。"""

from dataclasses import dataclass
from typing import Any, Protocol

from pydantic import AwareDatetime, Field

from creativity_service.core.context import AuthContext, Scope
from creativity_service.core.contracts import EvidenceRef
from creativity_service.core.primitives import Contract, ServiceError
from creativity_service.modules.tools.schemas import (
    BindingOption,
    EffectType,
    SourceType,
    ToolBinding,
    ToolDefinition,
)

EFFECT_LABELS = {"READ_ONLY": "只读", "IDEMPOTENT_WRITE": "幂等写入", "EXTERNAL_WRITE": "外部写入"}
SOURCE_LABELS = {"http": "HTTP 接口", "mcp": "MCP 工具", "builtin": "预置函数"}


class AdapterResult(Contract):
    data: Any
    source_request_id: str = Field(min_length=1, max_length=256)
    source_version: str = Field(min_length=1, max_length=128)
    observed_at: AwareDatetime
    evidence_refs: tuple[EvidenceRef, ...] = ()
    artifact_ids: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()
    cursor: str | None = None
    has_more: bool = False
    truncated: bool = False
    coverage: dict[str, Any] = Field(default_factory=dict)


@dataclass(frozen=True)
class AdapterRequest:
    context: AuthContext
    arguments: dict[str, Any]
    attempt_id: str
    definition: ToolDefinition


class ToolAdapter(Protocol):
    async def invoke(self, request: AdapterRequest) -> AdapterResult: ...


class ToolAdapterError(ServiceError):
    def __init__(
        self,
        code: str,
        message: str,
        *,
        retryable: bool = False,
        source_request_id: str | None = None,
    ) -> None:
        super().__init__(code, message, 504 if code == "TOOL_TIMEOUT" else 502)
        self.retryable, self.source_request_id = retryable, source_request_id


@dataclass(frozen=True)
class AdapterRegistration:
    key: str
    name: str
    source_type: SourceType
    implementation_version: str
    actual_effect: EffectType
    adapter: ToolAdapter
    channel_id: str | None = None
    environment: str | None = None
    connection_id: str | None = None
    sensitive: bool = True
    active: bool = True


class AdapterRegistry:
    def __init__(self) -> None:
        self._items: dict[tuple[str | None, str | None, str], AdapterRegistration] = {}

    def register(self, item: AdapterRegistration) -> None:
        if item.source_type != "builtin" and (not item.channel_id or not item.environment):
            raise ValueError("外部连接必须绑定渠道与环境")
        if item.channel_id == "system" or ((item.channel_id is None) != (item.environment is None)):
            raise ValueError("工具注册范围不正确")
        key = (item.channel_id, item.environment, item.key)
        if key in self._items:
            raise ValueError("适配器已登记，契约改变须使用新的实现版本和绑定编码")
        self._items[key] = item

    def resolve(self, scope: Scope, binding: ToolBinding) -> AdapterRegistration:
        item = self._items.get((scope.channel_id, scope.environment, binding.adapter_key))
        if item is None:
            item = self._items.get((None, None, binding.adapter_key))
        if item is None or not item.active:
            raise ServiceError("TOOL_UNAVAILABLE", "工具连接未登记或已停用", 503)
        if (
            item.implementation_version != binding.implementation_version
            or item.connection_id != binding.connection_id
        ):
            raise ServiceError("TOOL_UNAVAILABLE", "工具绑定与固定实现版本不符", 409)
        return item

    def validate(
        self, scope: Scope, definition: ToolDefinition, source_type: SourceType, *, executable: bool
    ) -> AdapterRegistration:
        item = self.resolve(scope, definition.binding)
        if item.source_type != source_type or item.actual_effect != definition.effect_type:
            raise ServiceError("TOOL_EFFECT_MISMATCH", "影响类型与来源实际副作用不一致", 422)
        if item.sensitive and not definition.subject_requirements.required:
            raise ServiceError("TOOL_INPUT_INVALID", "敏感工具必须要求受信业务主体", 422)
        if executable and definition.effect_type != "READ_ONLY":
            raise ServiceError("TOOL_WRITE_DISABLED", "写入工具尚未启用执行", 409)
        return item

    def options(self, scope: Scope) -> list[BindingOption]:
        return [
            BindingOption(
                binding=ToolBinding(
                    adapter_key=i.key,
                    implementation_version=i.implementation_version,
                    connection_id=i.connection_id,
                ),
                name=i.name,
                source_type=i.source_type,
                effect_type=i.actual_effect,
                effect_label=EFFECT_LABELS[i.actual_effect],
                execution_enabled=i.active and i.actual_effect == "READ_ONLY",
                unavailable_reason=(
                    None
                    if i.active and i.actual_effect == "READ_ONLY"
                    else "写入工具尚未启用执行"
                    if i.active
                    else "连接已停用"
                ),
            )
            for i in self._items.values()
            if (i.channel_id, i.environment)
            in {(None, None), (scope.channel_id, scope.environment)}
        ]
