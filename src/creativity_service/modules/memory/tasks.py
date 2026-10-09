"""记忆整理独立于对话事件，定时唤醒持久化整理任务。"""

import asyncio

from celery import shared_task

from creativity_service.modules.memory.vector_sync import VectorSync
from creativity_service.workers.runs import runtime


async def sweep() -> None:
    async with runtime() as (runs, channel_ids):
        for channel_id in channel_ids:
            await VectorSync(runs.engine).sweep(channel_id)
        if runs.memory_consolidation:
            for channel_id in channel_ids:
                await runs.memory_consolidation.sweep(channel_id)


def sweep_memory() -> None:
    asyncio.run(sweep())


sweep_memory_task = shared_task(name="memory.sweep", ignore_result=True)(sweep_memory)
