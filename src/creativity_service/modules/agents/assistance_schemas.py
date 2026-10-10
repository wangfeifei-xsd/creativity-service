"""智能协助只接收诉求和已有运行引用，不接受客户端回传候选配置。"""

from typing import Literal

from pydantic import Field

from creativity_service.core.primitives import Contract, Identifier
from creativity_service.modules.agents.schemas import AgentCreate, AgentDefinition


class AssistanceRequest(Contract):
    message: str = Field(min_length=1, max_length=8000)
    idempotency_key: Identifier
    model_route_id: Identifier | None = None
    agent_id: Identifier | None = None
    base_version_id: Identifier | None = None
    previous_run_id: Identifier | None = None


class AssistanceReply(Contract):
    message: str = Field(min_length=1, max_length=8000)
    proposal: AgentCreate | None = None


class AssistanceOutput(Contract):
    business_status: Literal["COMPLETED", "NEEDS_INPUT"]
    schema_version: Literal["1.0"]
    data: AssistanceReply
    warnings: list[str] = Field(default_factory=list, max_length=20)
    evidence_refs: list[str] = Field(default_factory=list, max_length=0)


class AssistanceTurn(Contract):
    run_id: str
    state: str
    state_label: str
    reply: AssistanceReply | None = None
    error: str | None = None
    base_definition: AgentDefinition | None = None


class AssistanceSaved(Contract):
    agent_id: str
    version_id: str


class AssistanceApply(Contract):
    """保存只使用运行中的候选，禁止请求正文附带配置或目标。"""
