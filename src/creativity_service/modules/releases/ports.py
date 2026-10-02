"""评测和调试由后续执行单元装配，未装配时保持拒绝。"""

from datetime import datetime
from typing import Protocol

from creativity_service.core.context import AuthContext
from creativity_service.core.database import UnitOfWork
from creativity_service.core.locking import ResourceKey
from creativity_service.core.primitives import Contract
from creativity_service.modules.agents.schemas import (
    AgentTestInput,
    AgentTestView,
    FrozenExecutionSpec,
)


class EvaluationEvidence(Contract):
    channel_id: str
    agent_id: str
    environment: str
    content_digest: str
    dependencies_digest: str
    report_digest: str
    passed: bool
    expires_at: datetime
    report_ids: tuple[str, ...]


class EvaluationGate(Protocol):
    def keys(self, context: AuthContext, refs: tuple[str, ...]) -> list[ResourceKey]: ...

    async def read(
        self, uow: UnitOfWork, context: AuthContext, refs: tuple[str, ...]
    ) -> EvaluationEvidence | None:
        """在锁下从受信报告重算摘要、有效期及阻断项，不调用网络、不接收客户端结果。"""
        ...


class AgentDebugRunner(Protocol):
    async def submit(
        self, context: AuthContext, snapshot: FrozenExecutionSpec, body: AgentTestInput
    ) -> AgentTestView:
        """17 按幂等键受理冻结草稿并记入预算账本，不能把校验成功当作运行成功。"""
        ...
