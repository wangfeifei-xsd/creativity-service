"""07 与 08、16、17 的装配边界，不提供独立模型测试执行器。"""

from typing import Protocol

from creativity_service.core.context import AuthContext
from creativity_service.core.database import UnitOfWork
from creativity_service.modules.models.schemas import DebugExecution, FrozenModel, PriceView


class DebugExecutor(Protocol):
    async def submit(self, context: AuthContext, execution: DebugExecution) -> str:
        """按 test_id 幂等受理 debug run，先预算准入，再为每次调用创建 Attempt。"""
        ...


class ModelPriceReader(Protocol):
    async def read(self, context: AuthContext, model_id: str) -> PriceView: ...
    async def require_priced(
        self, uow: UnitOfWork, context: AuthContext, models: list[FrozenModel]
    ) -> None:
        """在发布事务中检查有效价格；08 提供实际实现，不访问远端。"""
        ...
