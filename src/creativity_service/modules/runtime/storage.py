"""执行输入、冻结定义和来源登记共用运行租约与删除屏障。"""

from typing import Any

from creativity_service.core.context import TaskEnvelope
from creativity_service.core.database import Repository, transaction
from creativity_service.core.database.tables import metadata
from creativity_service.core.deletion import ContentRef, DeletionGuard
from creativity_service.core.locking import record_key
from creativity_service.core.primitives import ServiceError, digest
from creativity_service.modules.agents.schemas import FrozenExecutionSpec
from creativity_service.modules.runs.repositories import one, required
from creativity_service.modules.runs.schemas import Lease
from creativity_service.modules.runs.services import RunService


async def load_spec(runs: RunService, row: dict[str, Any]) -> FrozenExecutionSpec:
    context = runs.context(row)
    async with transaction(runs.engine, context.scope, runs.keys(context, row["id"])) as uow:
        await runs.guard(uow, row)
        stored = await required(
            uow.connection,
            "run_contents",
            context.scope.channel_id,
            run_id=row["id"],
            kind="execution_spec",
        )
        return FrozenExecutionSpec.model_validate(stored["payload"])


async def read_input(runs: RunService, lease: Lease) -> dict[str, Any]:
    original = await runs.before_progress(lease)
    context = runs.context(original)
    async with transaction(runs.engine, context.scope, runs.keys(context, lease.run_id)) as uow:
        row = await runs.locked_run(uow, lease.run_id)
        await runs.valid_lease(uow, row, lease)
        await runs.snapshot(uow, row)
        if row["state"] != "RUNNING":
            raise ServiceError("RUN_INACTIVE", "运行已停止", 409)
        return dict(
            (
                await required(
                    uow.connection,
                    "run_contents",
                    context.scope.channel_id,
                    id=row["input_ref"],
                    run_id=row["id"],
                )
            )["payload"]
        )


async def record_inputs(
    runs: RunService, lease: Lease, key: str, payload: dict[str, Any], refs: list[ContentRef]
) -> None:
    original = await runs.load(TaskEnvelope(channel_id=lease.scope.channel_id, run_id=lease.run_id))
    context = runs.context(original)
    links = [(digest([lease.run_id, r.resource_type, r.resource_id]), r) for r in refs]
    keys = runs.keys(context, lease.run_id) + [
        record_key(context.scope.channel_id, "source_links", i) for i, _ in links
    ]
    async with transaction(runs.engine, context.scope, keys) as uow:
        row = await runs.locked_run(uow, lease.run_id)
        await runs.valid_lease(uow, row, lease)
        await runs.guard(uow, row)
        if row["state"] != "RUNNING":
            raise ServiceError("RUN_INACTIVE", "运行已停止", 409)
        guard = DeletionGuard(context.scope)
        for identifier, ref in links:
            await guard.check(uow, [ref])
            if not await Repository(metadata.tables["source_links"], context.scope).get(
                uow.connection, identifier
            ):
                await guard.link(uow, identifier, ref, ContentRef("run", lease.run_id))
        # 相同实际调用只登记一次；不同轮次保留各自输入和文件加载证据。
        if (
            await one(
                uow.connection,
                "run_contents",
                context.scope.channel_id,
                run_id=row["id"],
                kind="inputs:" + key,
            )
            is None
        ):
            await runs.content(uow, row, "inputs:" + key, payload)
