"""正式 Agent 目录、主体名称和受控摘要生成由所属模块装配。"""

from typing import Protocol

from creativity_service.core.context import AuthContext
from creativity_service.core.database import UnitOfWork
from creativity_service.core.locking import ResourceKey
from creativity_service.modules.conversations.schemas import AgentChoice


class AgentDirectory(Protocol):
    async def list_available(self, context: AuthContext) -> list[AgentChoice]: ...


class SubjectNames(Protocol):
    async def name(self, context: AuthContext) -> str | None: ...


class RetentionReader(Protocol):
    def keys(self, context: AuthContext) -> list[ResourceKey]: ...
    async def days(self, uow: UnitOfWork, context: AuthContext) -> int: ...


class SummaryGenerator(Protocol):
    async def generate(
        self, context: AuthContext, messages: list[dict[str, str]]
    ) -> tuple[str, str]:
        """17 经统一预算及计量入口生成，返回摘要和已成功的生成运行标识。"""
        ...
