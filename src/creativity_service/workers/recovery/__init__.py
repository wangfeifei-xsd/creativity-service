"""渠道内补偿排队超时、失效租约及终态占用释放。"""

from creativity_service.core.context import TaskEnvelope
from creativity_service.core.primitives import ServiceError
from creativity_service.modules.runs.repositories import rows
from creativity_service.modules.runs.schemas import TERMINAL
from creativity_service.modules.runs.services import RunService


class Recovery:
    def __init__(self, runs: RunService) -> None:
        self.runs = runs

    async def scan(self, channel_id: str) -> dict[str, int]:
        async with self.runs.engine.connect() as connection:
            records = await rows(connection, "runs", channel_id)
        counts = {"checked": 0, "deferred": 0}
        for row in records:
            if row["state"] in TERMINAL and row["resources_released"]:
                continue
            try:
                await self.runs.reconcile_run(TaskEnvelope(channel_id=channel_id, run_id=row["id"]))
                counts["checked"] += 1
            except ServiceError as exc:
                if exc.status != 503:
                    raise
                counts["deferred"] += 1
        return counts
