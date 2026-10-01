"""运行受理方组合预算与版本快照的接入示例，由方案 11 传入实际任务写入器。"""

from collections.abc import Awaitable, Callable
from typing import Any

from sqlalchemy.ext.asyncio import AsyncEngine

from creativity_service.core.context import AuthContext, Authorization
from creativity_service.core.contracts import ReleaseSnapshot
from creativity_service.core.contracts.ports import BudgetService, require_component
from creativity_service.core.database import UnitOfWork, transaction
from creativity_service.core.locking import ResourceKey
from creativity_service.core.versioning import VersionService


async def admit_run(
    engine: AsyncEngine,
    context: AuthContext,
    authorization: Authorization,
    versions: VersionService,
    budget: BudgetService | None,
    run_id: str,
    version_ids: list[str],
    output_schema: dict[str, Any],
    run_keys: list[ResourceKey],
    persist_run_idempotency_outbox: Callable[[UnitOfWork, ReleaseSnapshot], Awaitable[None]],
) -> ReleaseSnapshot:
    # 实际输入与发布解析由受理服务完成；所有网络授权在短事务前结束。
    await authorization.require(context, "run:create", run_id)
    budget = require_component(budget, "预算准入服务")
    keys = [
        *run_keys,
        *budget.admission_keys(context, run_id),
        *versions.snapshot_keys(context.scope, run_id, version_ids),
    ]
    async with transaction(engine, context.scope, keys) as uow:
        snapshot = await versions.snapshot_in(
            uow, context, run_id, version_ids, "production", output_schema
        )
        await budget.admit(uow, context, run_id)
        await persist_run_idempotency_outbox(uow, snapshot)
    # 提交后由可靠投递服务处理队列；任何一步失败时所有数据库写入一起回滚。
    return snapshot
