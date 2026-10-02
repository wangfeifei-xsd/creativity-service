"""运行与发布解析、会话轮次、流程执行器之间的内部边界。"""

from typing import Any, Protocol

from creativity_service.core.context import AuthContext, TaskEnvelope
from creativity_service.core.database import UnitOfWork
from creativity_service.core.locking import ResourceKey
from creativity_service.core.primitives import RunInput
from creativity_service.modules.runs.schemas import Lease, ResolvedDefinition


class DefinitionResolver(Protocol):
    async def resolve(self, context: AuthContext, request: RunInput) -> ResolvedDefinition: ...


class TurnHooks(Protocol):
    def keys(self, context: AuthContext, conversation_id: str) -> list[ResourceKey]: ...
    async def admit(
        self, uow: UnitOfWork, context: AuthContext, run: dict[str, Any], request: RunInput
    ) -> None:
        """同事务验证会话及消息幂等、分配轮次；不得发送 HTTP 或另开事务。"""
        ...

    async def finish(self, uow: UnitOfWork, context: AuthContext, run: dict[str, Any]) -> None:
        """终态事务内更新助手消息；补偿可重复调用。"""
        ...


class Publisher(Protocol):
    async def publish(self, message: TaskEnvelope) -> None: ...


class ConversationTurnHooks(TurnHooks, Protocol):
    """12 的扩展钩子；受理和片段服务在调用方短事务中使用。"""

    async def replay(
        self, uow: UnitOfWork, context: AuthContext, request: RunInput
    ) -> str | None: ...
    async def prepare(
        self,
        uow: UnitOfWork,
        context: AuthContext,
        definition: ResolvedDefinition,
        request: RunInput,
    ) -> None: ...
    async def project(
        self,
        uow: UnitOfWork,
        context: AuthContext,
        run: dict[str, Any],
        event_type: str,
        payload: dict[str, Any],
    ) -> None: ...
    async def rerun_request(
        self,
        uow: UnitOfWork,
        context: AuthContext,
        run: dict[str, Any],
        request: RunInput,
        key: str,
    ) -> RunInput: ...


class Executor(Protocol):
    async def execute(self, context: AuthContext, lease: Lease) -> None:
        """17 注入流程执行器；所有有效进度通过租约服务提交。"""
        ...
