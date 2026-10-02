"""供方案 16、17、25 装配的端口；外部业务核验应先于记忆短事务完成。"""

from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

from creativity_service.core.context import AuthContext
from creativity_service.core.contracts import ToolResult
from creativity_service.core.database import UnitOfWork
from creativity_service.modules.memory.schemas import (
    CandidateInput,
    MemoryLoad,
    MemoryPolicy,
    MemorySelection,
    MemoryView,
    SourceInput,
)


@dataclass(frozen=True)
class SourceState:
    name: str
    authority: str
    trust_level: int
    observed_at: datetime
    fact_keys: frozenset[str] = frozenset()


class MemorySourceReader(Protocol):
    async def resolve(
        self, uow: UnitOfWork, context: AuthContext, source: SourceInput
    ) -> SourceState | None:
        """仅读取当前事务可见的本地来源；不能在持锁期间访问外部业务系统。"""
        ...


class MemoryRuntimePort(Protocol):
    async def write_fact(
        self, context: AuthContext, run_id: str, key: str, result: ToolResult
    ) -> MemoryView | None: ...

    async def validate_agent_policy(self, context: AuthContext, policy: MemoryPolicy) -> None: ...

    async def write_candidate(
        self, context: AuthContext, run_id: str, body: CandidateInput
    ) -> MemoryView | None: ...

    async def select(
        self, context: AuthContext, run_id: str, keys: list[str], current_keys: list[str]
    ) -> MemorySelection: ...

    async def load(
        self,
        context: AuthContext,
        run_id: str,
        selection: MemorySelection,
        current_keys: list[str],
        required_fact_keys: list[str],
    ) -> MemoryLoad: ...
