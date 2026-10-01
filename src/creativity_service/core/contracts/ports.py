"""关键服务交接端口；未装配时拒绝调用，测试替身仅在测试代码中。"""

from typing import Protocol

from creativity_service.core.context import AuthContext
from creativity_service.core.contracts import Admission, BudgetReservation, UsageEvent
from creativity_service.core.database import UnitOfWork
from creativity_service.core.locking import ResourceKey
from creativity_service.core.primitives import unavailable


class BudgetService(Protocol):
    def admission_keys(self, context: AuthContext, run_id: str) -> list[ResourceKey]: ...
    def reservation_keys(self, context: AuthContext, attempt_id: str) -> list[ResourceKey]: ...
    async def admit(self, uow: UnitOfWork, context: AuthContext, run_id: str) -> Admission: ...
    async def reserve(
        self, uow: UnitOfWork, context: AuthContext, attempt_id: str
    ) -> BudgetReservation: ...
    async def record_usage(self, uow: UnitOfWork, event: UsageEvent) -> None: ...


def require_component[T](component: T | None, name: str) -> T:
    if component is None:
        raise unavailable(name)
    return component
