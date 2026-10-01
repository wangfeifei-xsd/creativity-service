"""渠道状态转移、影响预览及持久化生命周期交接。"""

from typing import Literal

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncConnection

from creativity_service.core.auth.authentication import AdminSession
from creativity_service.core.context import Scope
from creativity_service.core.database import transaction
from creativity_service.core.locking import record_key
from creativity_service.core.primitives import ServiceError, new_id, unavailable, utcnow
from creativity_service.modules.channels.ports import TaskLifecycleGuard
from creativity_service.modules.channels.repositories import required, rows, save
from creativity_service.modules.channels.schemas import (
    ChannelView,
    ImpactView,
    LifecycleAction,
    LifecycleEvent,
)
from creativity_service.modules.channels.services import ChannelService
from creativity_service.modules.channels.tables import metadata
from creativity_service.modules.iam.repositories import policy_key


class LifecycleService:
    def __init__(self, channels: ChannelService) -> None:
        self.channels, self.repository = channels, channels.repository
        self.task_guard: TaskLifecycleGuard | None = None

    def register_tasks(self, guard: TaskLifecycleGuard) -> None:
        if self.task_guard is not None:
            raise ValueError("任务生命周期检查已经登记")
        self.task_guard = guard

    async def unfinished(self, connection: AsyncConnection, channel_id: str) -> int | None:
        if self.task_guard:
            count = await self.task_guard.unfinished(connection, channel_id)
            if count < 0:
                raise ValueError("未终结任务数量不能为负数")
            return count
        # 11 尚未安装时不可能产生任务；一旦存在任务表，必须登记真实检查，不能按零处理。
        if await connection.scalar(text("SELECT to_regclass('runs')")) is not None:
            return None
        return 0

    async def impact(
        self, connection: AsyncConnection, channel_id: str, action: LifecycleAction
    ) -> ImpactView:
        channel = await required(connection, "channels", channel_id, id=channel_id)
        active_keys = sum(
            k["status"] == "ACTIVE" and k["expires_at"] > utcnow()
            for k in await rows(connection, "channel_keys", channel_id)
        )
        clients = sum(
            c["status"] == "ACTIVE" for c in await rows(connection, "service_clients", channel_id)
        )
        unfinished = await self.unfinished(connection, channel_id)
        blockers = []
        allowed = {
            "suspend": {"ACTIVE"},
            "resume": {"SUSPENDED"},
            "archive": {"ACTIVE", "SUSPENDED"},
        }
        if channel["status"] not in allowed[action]:
            blockers.append("当前渠道状态不允许此操作")
        if action == "archive":
            if active_keys:
                blockers.append("请先吊销全部有效接入 Key")
            if unfinished is None:
                blockers.append("未能核实未终结任务")
            elif unfinished:
                blockers.append("请先结束或取消未终结任务")
        return ImpactView(
            channel_id=channel_id,
            channel_name=channel["name"],
            action=action,
            revision=channel["revision"],
            active_keys=active_keys,
            active_clients=clients,
            unfinished_tasks=unfinished,
            blockers=blockers,
            can_execute=not blockers,
        )

    async def preview(
        self, session: AdminSession, channel_id: str, action: LifecycleAction
    ) -> ImpactView:
        await self.channels.authorize(session, channel_id, "channel:manage")
        keys = [policy_key("system"), policy_key(channel_id)] + (
            self.task_guard.keys(channel_id) if self.task_guard else []
        )
        async with transaction(
            self.repository.engine, self.channels.scope(session, channel_id), keys
        ) as uow:
            await self.channels.locked(uow, session, "channel:manage", writable=False)
            return await self.impact(uow.connection, channel_id, action)

    async def change(
        self, session: AdminSession, channel_id: str, action: LifecycleAction, revision: int
    ) -> ChannelView:
        await self.channels.authorize(session, channel_id, "channel:manage")
        event_id = new_id("audit")
        keys = self.channels.keys(channel_id, "channels", channel_id, event_id) + (
            self.task_guard.keys(channel_id) if self.task_guard else []
        )
        async with transaction(
            self.repository.engine, self.channels.scope(session, channel_id), keys
        ) as uow:
            old = await self.channels.locked(uow, session, "channel:manage")
            impact = await self.impact(uow.connection, channel_id, action)
            if action == "archive" and impact.unfinished_tasks is None:
                raise unavailable("任务归档检查")
            if not impact.can_execute:
                raise ServiceError("CHANNEL_TRANSITION_BLOCKED", "；".join(impact.blockers), 409)
            row = await save(
                uow,
                "channels",
                channel_id,
                {
                    "status": {"suspend": "SUSPENDED", "resume": "ACTIVE", "archive": "ARCHIVED"}[
                        action
                    ],
                    "archived_at": utcnow() if action == "archive" else None,
                },
                revision,
            )
            await self.channels.event(
                uow, event_id, session, f"channel:{action}", "channel", row, old["status"]
            )
        return await self.channels.detail(session, channel_id)

    async def pending(
        self, channel_id: str, consumer: Literal["runs", "retention"], limit: int = 100
    ) -> list[LifecycleEvent]:
        Scope(channel_id=channel_id, environment="dev")
        if consumer not in {"runs", "retention"} or not 1 <= limit <= 200:
            raise ValueError("生命周期消费参数不正确")
        table = metadata.tables["channel_lifecycle_events"]
        async with self.repository.engine.connect() as connection:
            result = await connection.execute(
                select(table)
                .where(
                    table.c.channel_id == channel_id, ~table.c.acknowledgements.has_key(consumer)
                )
                .order_by(table.c.created_at, table.c.id)
                .limit(limit)
            )
            return [
                LifecycleEvent(
                    event_id=r["id"],
                    channel_id=r["channel_id"],
                    event_type=r["event_type"],
                    target_type=r["target_type"],
                    target_id=r["target_id"],
                    environment=r["environment"],
                    occurred_at=r["created_at"],
                    **r["payload"],
                )
                for r in result.mappings()
            ]

    async def acknowledge(
        self, channel_id: str, event_id: str, consumer: Literal["runs", "retention"]
    ) -> None:
        if consumer not in {"runs", "retention"}:
            raise ValueError("生命周期消费模块未登记")
        scope = Scope(channel_id=channel_id, environment="dev")
        async with transaction(
            self.repository.engine,
            scope,
            [policy_key(channel_id), record_key(channel_id, "channel_lifecycle_events", event_id)],
        ) as uow:
            row = await required(
                uow.connection, "channel_lifecycle_events", channel_id, id=event_id
            )
            if consumer not in row["acknowledgements"]:
                await save(
                    uow,
                    "channel_lifecycle_events",
                    event_id,
                    {
                        "acknowledgements": {
                            **row["acknowledgements"],
                            consumer: utcnow().isoformat(),
                        },
                    },
                    row["revision"],
                )
