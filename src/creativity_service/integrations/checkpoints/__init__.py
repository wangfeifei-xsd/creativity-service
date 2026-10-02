"""LangGraph 自有 SQLAlchemy 存储，不运行框架建表或数据库 upsert。"""

import base64
from collections.abc import AsyncIterator, Sequence
from typing import Any

from langchain_core.runnables import RunnableConfig
from langgraph.checkpoint.base import (
    BaseCheckpointSaver,
    ChannelVersions,
    Checkpoint,
    CheckpointMetadata,
    CheckpointTuple,
)

from creativity_service.core.database import transaction
from creativity_service.core.primitives import ServiceError, digest
from creativity_service.modules.runs.repositories import one, required, rows, save
from creativity_service.modules.runs.schemas import Lease
from creativity_service.modules.runs.services import RunService


class SQLAlchemySaver(BaseCheckpointSaver[int]):
    """每个实例仅绑定一个运行租约；读写均复核授权、删除屏障及租约代次。"""

    def __init__(self, runs: RunService, lease: Lease) -> None:
        super().__init__()
        self.runs, self.lease = runs, lease

    def identify(self, config: RunnableConfig) -> tuple[str, str | None]:
        values = config.get("configurable", {})
        if values.get("thread_id") != self.lease.run_id:
            raise ServiceError("CHECKPOINT_SCOPE_INVALID", "恢复点不属于当前运行", 403)
        return "langgraph:" + values.get("checkpoint_ns", ""), values.get("checkpoint_id")

    def encode(self, value: Any) -> dict[str, str]:
        kind, data = self.serde.dumps_typed(value)
        return {"kind": kind, "data": base64.b64encode(data).decode()}

    def decode(self, value: dict[str, str]) -> Any:
        return self.serde.loads_typed((value["kind"], base64.b64decode(value["data"])))

    async def read(self, config: RunnableConfig) -> list[CheckpointTuple]:
        namespace, checkpoint_id = self.identify(config)
        original = await self.runs.before_progress(self.lease)
        context = self.runs.context(original)
        async with transaction(
            self.runs.engine, context.scope, self.runs.keys(context, self.lease.run_id)
        ) as uow:
            run = await self.runs.locked_run(uow, self.lease.run_id)
            await self.runs.valid_lease(uow, run, self.lease)
            if await self.runs.expire(uow, run):
                raise ServiceError("RUN_INACTIVE", "已终结运行不能读取恢复点", 409)
            await self.runs.snapshot(uow, run)
            points = await rows(
                uow.connection,
                "checkpoints",
                context.scope.channel_id,
                run_id=run["id"],
                namespace=namespace,
            )
            result = []
            for point in sorted(points, key=lambda p: p["checkpoint_key"], reverse=True):
                if checkpoint_id and checkpoint_id != point["checkpoint_key"]:
                    continue
                if point["release_snapshot_id"] != run["release_snapshot_id"]:
                    raise ServiceError("SNAPSHOT_INVALID", "恢复点与运行快照不一致", 409)
                payload = (
                    await required(
                        uow.connection,
                        "run_contents",
                        context.scope.channel_id,
                        id=point["state_ref"],
                        run_id=run["id"],
                    )
                )["payload"]
                cfg: RunnableConfig = {
                    "configurable": {
                        "thread_id": run["id"],
                        "checkpoint_ns": namespace.removeprefix("langgraph:"),
                        "checkpoint_id": point["checkpoint_key"],
                    }
                }
                writes = await rows(
                    uow.connection,
                    "checkpoints",
                    context.scope.channel_id,
                    run_id=run["id"],
                    namespace=namespace + ":writes",
                    parent_key=point["checkpoint_key"],
                )
                pending: list[tuple[str, str, Any]] = []
                for write in sorted(writes, key=lambda w: w["created_at"]):
                    data = (
                        await required(
                            uow.connection,
                            "run_contents",
                            context.scope.channel_id,
                            id=write["state_ref"],
                            run_id=run["id"],
                        )
                    )["payload"]
                    pending.extend(
                        (data["task_id"], channel, self.decode(value))
                        for channel, value in data["writes"]
                    )
                parent: RunnableConfig | None = None
                if point["parent_key"]:
                    parent = {
                        "configurable": {
                            **cfg["configurable"],
                            "checkpoint_id": point["parent_key"],
                        }
                    }
                result.append(
                    CheckpointTuple(
                        cfg,
                        self.decode(payload["checkpoint"]),
                        payload["metadata"],
                        parent,
                        pending,
                    )
                )
            return result

    async def aget_tuple(self, config: RunnableConfig) -> CheckpointTuple | None:
        items = await self.read(config)
        return items[0] if items else None

    async def alist(
        self,
        config: RunnableConfig | None,
        *,
        filter: dict[str, Any] | None = None,
        before: RunnableConfig | None = None,
        limit: int | None = None,
    ) -> AsyncIterator[CheckpointTuple]:
        if config is None:
            raise ServiceError("CHECKPOINT_SCOPE_INVALID", "查询恢复点须指定运行", 403)
        before_id = self.identify(before)[1] if before else None
        count = 0
        for item in await self.read(config):
            if before_id and item.checkpoint["id"] >= before_id:
                continue
            if filter and any(item.metadata.get(k) != v for k, v in filter.items()):
                continue
            if limit is not None and count >= limit:
                break
            yield item
            count += 1

    async def store(
        self, namespace: str, key: str, parent: str | None, payload: dict[str, Any]
    ) -> None:
        original = await self.runs.before_progress(self.lease)
        context = self.runs.context(original)
        identifier = digest([context.scope.model_dump(), self.lease.run_id, namespace, key])
        async with transaction(
            self.runs.engine, context.scope, self.runs.keys(context, self.lease.run_id)
        ) as uow:
            run = await self.runs.locked_run(uow, self.lease.run_id)
            await self.runs.valid_lease(uow, run, self.lease)
            if await self.runs.expire(uow, run):
                raise ServiceError("RUN_INACTIVE", "运行已终结，不能保存恢复点", 409)
            await self.runs.snapshot(uow, run)
            old = await one(uow.connection, "checkpoints", context.scope.channel_id, id=identifier)
            if old:
                content = await required(
                    uow.connection, "run_contents", context.scope.channel_id, id=old["state_ref"]
                )
                if content["payload"] != payload:
                    raise ServiceError("CHECKPOINT_CONFLICT", "恢复点重复提交内容不同", 409)
                return
            await save(
                uow,
                "checkpoints",
                identifier,
                {
                    "run_id": run["id"],
                    "namespace": namespace,
                    "checkpoint_key": key,
                    "parent_key": parent,
                    "lease_version": self.lease.lease_version,
                    "release_snapshot_id": run["release_snapshot_id"],
                    "state_ref": await self.runs.content(uow, run, "checkpoint", payload),
                    "metadata": {},
                },
            )

    async def aput(
        self,
        config: RunnableConfig,
        checkpoint: Checkpoint,
        metadata: CheckpointMetadata,
        new_versions: ChannelVersions,
    ) -> RunnableConfig:
        namespace, parent = self.identify(config)
        await self.store(
            namespace,
            checkpoint["id"],
            parent,
            {"checkpoint": self.encode(checkpoint), "metadata": metadata},
        )
        return {
            "configurable": {**config.get("configurable", {}), "checkpoint_id": checkpoint["id"]}
        }

    async def aput_writes(
        self,
        config: RunnableConfig,
        writes: Sequence[tuple[str, Any]],
        task_id: str,
        task_path: str = "",
    ) -> None:
        namespace, parent = self.identify(config)
        # 分任务、通道、序号去重；同一任务可能分批交付写入。
        for index, (channel, value) in enumerate(writes):
            await self.store(
                namespace + ":writes",
                digest([parent, task_id, channel, index]),
                parent,
                {"task_id": task_id, "writes": [[channel, self.encode(value)]]},
            )

    async def adelete_thread(self, thread_id: str) -> None:
        raise ServiceError("DELETION_REQUIRED", "恢复内容须经统一删除服务清理", 403)
