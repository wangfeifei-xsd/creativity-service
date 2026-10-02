"""由 07、16、17 装配的受信端口；调试提交幂等且用量进入统一预算。"""

from typing import Any, Protocol

from creativity_service.core.context import AuthContext
from creativity_service.core.database import UnitOfWork
from creativity_service.core.deletion import ContentRef
from creativity_service.modules.prompts.schemas import (
    PromptDebugDescriptor,
    PromptDebugEvidence,
    PromptDebugRun,
    PromptRuntimeInput,
)


class PromptDebugRunner(Protocol):
    async def submit(
        self, context: AuthContext, descriptor: PromptDebugDescriptor
    ) -> PromptDebugRun:
        """按渠道和 test_id 幂等受理；创建 debug 运行、预算、快照与任务。"""
        ...


class PromptEvidenceReader(Protocol):
    async def read(
        self, uow: UnitOfWork, context: AuthContext, test: dict[str, Any]
    ) -> PromptDebugEvidence | None:
        """同一短事务查询真实运行和账本，不调用外部网络，不信任请求中的成功状态。"""
        ...


class PromptContextProvider(Protocol):
    async def resolve(
        self, context: AuthContext, input: PromptRuntimeInput
    ) -> tuple[PromptRuntimeInput, tuple[ContentRef, ...]]:
        """按当前主体授权读取工具与记忆，并返回来源引用；不得覆盖调用输入。"""
        ...
