"""向 14、16、17、18 交接的受控端口；缺少运行服务时不得执行。"""

from typing import Protocol

from creativity_service.core.context import AuthContext
from creativity_service.core.contracts import Attempt, ToolResult
from creativity_service.modules.tools.schemas import (
    RunToolGrant,
    ToolExecution,
    ToolTestInput,
    ToolTestResult,
)


class ToolDebugPort(Protocol):
    async def create_debug_run(
        self, context: AuthContext, version_id: str, body: ToolTestInput
    ) -> ToolTestResult:
        """创建固定草稿修订的 debug run，再通过统一工具执行入口执行。"""
        ...


class ToolRunPort(ToolDebugPort, Protocol):
    async def authorize_call(self, context: AuthContext, call: ToolExecution) -> RunToolGrant:
        """重复复核持久化运行、步骤和限额；此入口不重复扣减调用次数。"""
        ...

    async def start_attempt(self, context: AuthContext, attempt: Attempt) -> None:
        """每次实际调用前原子占用剩余次数、时限与预算，并持久化独立尝试。"""
        ...

    async def finish_attempt(self, context: AuthContext, attempt: Attempt) -> None: ...


class ToolCache(Protocol):
    async def get(self, key: str) -> ToolResult | None: ...
    async def put(self, key: str, result: ToolResult, ttl_seconds: int) -> None: ...
