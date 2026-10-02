"""事务外发布队列唤醒；投递声明超时及确认丢失可安全重复发布。"""

from datetime import timedelta

from creativity_service.core.context import TaskEnvelope
from creativity_service.core.database import assert_external_io_allowed, transaction
from creativity_service.core.primitives import utcnow
from creativity_service.modules.runs.ports import Publisher
from creativity_service.modules.runs.repositories import required, rows, save
from creativity_service.modules.runs.schemas import TERMINAL
from creativity_service.modules.runs.services import RunService


class Dispatcher:
    def __init__(self, runs: RunService, publisher: Publisher) -> None:
        self.runs, self.publisher = runs, publisher

    async def dispatch(self, message: TaskEnvelope) -> bool:
        original = await self.runs.load(message)
        context = self.runs.context(original)
        async with transaction(
            self.runs.engine, context.scope, self.runs.keys(context, message.run_id)
        ) as uow:
            row = await self.runs.locked_run(uow, message.run_id)
            outbox = await required(
                uow.connection, "dispatch_outbox", message.channel_id, run_id=message.run_id
            )
            if (
                row["state"] in TERMINAL
                or outbox["state"] in {"DONE", "EXECUTING"}
                or outbox["next_attempt_at"] > utcnow()
            ):
                return False
            if await self.runs.expire(uow, row):
                version = None
            else:
                version = outbox["delivery_version"] + 1
                await save(
                    uow,
                    "dispatch_outbox",
                    outbox["id"],
                    {
                        "state": "PUBLISHING",
                        "delivery_version": version,
                        "dispatch_attempts": outbox["dispatch_attempts"] + 1,
                        "next_attempt_at": utcnow() + timedelta(seconds=30),
                    },
                )
        await self.runs.after_commit(row)
        if version is None:
            return False
        error = None
        try:
            assert_external_io_allowed()
            await self.publisher.publish(message)
        except Exception:
            error = "BROKER_UNAVAILABLE"
        if error is None:
            self.runs.fault("published_before_confirmation")
        async with transaction(
            self.runs.engine, context.scope, self.runs.keys(context, message.run_id)
        ) as uow:
            current = await required(
                uow.connection, "dispatch_outbox", message.channel_id, run_id=message.run_id
            )
            if current["delivery_version"] == version and current["state"] == "PUBLISHING":
                await save(
                    uow,
                    "dispatch_outbox",
                    current["id"],
                    {
                        "state": "PENDING" if error else "PUBLISHED",
                        "last_error": error,
                        "next_attempt_at": utcnow()
                        + timedelta(seconds=min(60, 2 ** min(current["dispatch_attempts"], 5))),
                    },
                )
        return error is None

    async def scan(self, channel_id: str) -> int:
        async with self.runs.engine.connect() as connection:
            intents = await rows(connection, "dispatch_outbox", channel_id)
        count = 0
        for intent in intents:
            if (
                intent["state"] not in {"DONE", "EXECUTING"}
                and intent["next_attempt_at"] <= utcnow()
            ):
                count += await self.dispatch(
                    TaskEnvelope(channel_id=channel_id, run_id=intent["run_id"])
                )
        return count
