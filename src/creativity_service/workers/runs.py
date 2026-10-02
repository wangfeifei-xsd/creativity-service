"""Celery 消息只唤醒持久化任务；流程执行器由正式运行装配注入。"""

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from uuid import uuid4

from celery import current_app, shared_task

from creativity_service.core.config import Settings
from creativity_service.core.context import TaskEnvelope
from creativity_service.core.database import assert_external_io_allowed
from creativity_service.core.infrastructure import Infrastructure
from creativity_service.core.versioning import VersionService
from creativity_service.modules.budgets.services import BudgetService
from creativity_service.modules.channels.assembly import build_channel_services
from creativity_service.modules.conversations.hooks import ConversationHooks
from creativity_service.modules.runs.assembly import build_run_service
from creativity_service.modules.runs.ports import Executor
from creativity_service.modules.runs.services import RunService
from creativity_service.modules.usage.services import UsageService
from creativity_service.workers.dispatcher import Dispatcher
from creativity_service.workers.executor import execute_message
from creativity_service.workers.recovery import Recovery

executor: Executor | None = None


class CeleryPublisher:
    async def publish(self, message: TaskEnvelope) -> None:
        assert_external_io_allowed()
        await asyncio.to_thread(
            current_app.send_task,
            "runs.execute",
            kwargs={"message": message.model_dump(mode="json")},
            retry=False,
            ignore_result=True,
        )


@asynccontextmanager
async def runtime() -> AsyncIterator[tuple[RunService, list[str]]]:
    settings = Settings()
    infrastructure = Infrastructure(settings)
    try:
        iam, channels = build_channel_services(
            infrastructure.engine,
            infrastructure.redis_clients["redis_auth"],
            settings.redis_key_prefix,
        )
        budgets = BudgetService(infrastructure.engine)
        runs = build_run_service(
            infrastructure.engine,
            iam,
            VersionService(infrastructure.engine, iam.authorization),
            budgets,
            UsageService(infrastructure.engine, budgets),
            turns=ConversationHooks(),
        )
        async with infrastructure.engine.connect() as connection:
            channel_ids = await channels.channels.repository.directory(connection)
        yield runs, channel_ids
    finally:
        await infrastructure.close()


async def sweep() -> None:
    async with runtime() as (runs, channel_ids):
        for channel_id in channel_ids:
            await Recovery(runs).scan(channel_id)
            await Dispatcher(runs, CeleryPublisher()).scan(channel_id)


async def execute(message: dict[str, str]) -> None:
    envelope = TaskEnvelope.model_validate(message)
    async with runtime() as (runs, _):
        await execute_message(runs, envelope, f"worker_{uuid4().hex}", executor)


def execute_run(message: dict[str, str]) -> None:
    asyncio.run(execute(message))


def sweep_runs() -> None:
    asyncio.run(sweep())


execute_run_task = shared_task(
    name="runs.execute", ignore_result=True, acks_late=True, reject_on_worker_lost=True
)(execute_run)
sweep_runs_task = shared_task(name="runs.sweep", ignore_result=True)(sweep_runs)
