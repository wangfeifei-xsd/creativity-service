"""每次唤醒独立核对身份并清理上下文，执行器在事务外运行。"""

import asyncio
from contextlib import suppress

from creativity_service.core.context import TaskEnvelope, current_context, task_context
from creativity_service.core.primitives import unavailable
from creativity_service.modules.runs.ports import Executor
from creativity_service.modules.runs.services import RunService


async def execute_message(
    runs: RunService, message: TaskEnvelope, worker_id: str, executor: Executor | None
) -> None:
    current_context.set(None)
    heartbeat = None
    try:
        if executor is None:
            raise unavailable("运行流程执行器")
        lease = await runs.claim_lease(message, worker_id)
        if lease is None:
            return
        async with task_context(message, runs) as context:
            owner = asyncio.current_task()

            async def renew() -> None:
                try:
                    while True:
                        await asyncio.sleep(max(0.1, runs.lease_seconds / 3))
                        if await runs.heartbeat(lease) is None:
                            if owner:
                                owner.cancel()
                            return
                except Exception:
                    if owner:
                        owner.cancel()
                    raise

            heartbeat = asyncio.create_task(renew())
            await executor.execute(context, lease)
    finally:
        try:
            if heartbeat:
                heartbeat.cancel()
                with suppress(asyncio.CancelledError):
                    await heartbeat
        finally:
            current_context.set(None)
