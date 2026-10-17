"""技能智能协助只接收需求；候选与保存目标由服务端运行记录确定。"""

from typing import Literal

from pydantic import Field

from creativity_service.core.primitives import Contract, Identifier
from creativity_service.modules.skills.schemas import SkillCreate


class SkillAssistanceRequest(Contract):
    message: str = Field(min_length=1, max_length=8000)
    idempotency_key: Identifier
    model_route_id: Identifier | None = None
    skill_id: Identifier | None = None
    base_revision: int | None = Field(default=None, ge=1)
    previous_run_id: Identifier | None = None


class SkillProposal(SkillCreate):
    instructions: str = Field(min_length=1, max_length=40000)


class SkillAssistanceReply(Contract):
    message: str = Field(min_length=1, max_length=8000)
    proposal: SkillProposal | None = None


class SkillAssistanceOutput(Contract):
    business_status: Literal["COMPLETED", "NEEDS_INPUT"]
    schema_version: Literal["1.0"]
    data: SkillAssistanceReply
    warnings: list[str] = Field(default_factory=list, max_length=20)
    evidence_refs: list[str] = Field(default_factory=list, max_length=0)


class SkillAssistanceTurn(Contract):
    run_id: str
    state: str
    state_label: str
    reply: SkillAssistanceReply | None = None
    error: str | None = None
    base: SkillProposal | None = None


class SkillAssistanceSaved(Contract):
    skill_id: str


class SkillAssistanceApply(Contract):
    """不允许客户端回传候选、目标或权限字段。"""


class SkillAssistanceRoute(Contract):
    route_id: str
    name: str
