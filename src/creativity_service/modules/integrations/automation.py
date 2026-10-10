"""定时与批量条目先持久化，再以稳定幂等键进入统一运行。"""

import secrets
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import select

from creativity_service.core.context import AuthContext, Scope
from creativity_service.core.database import Repository, UnitOfWork, transaction
from creativity_service.core.database.soft_delete import active_rows
from creativity_service.core.deletion import ContentRef, DeletionGuard, content_key
from creativity_service.core.locking import ResourceKey, record_key
from creativity_service.core.primitives import RunInput, ServiceError, digest, new_id, utcnow
from creativity_service.modules.agents.access import locked_require
from creativity_service.modules.agents.repositories import repository as agent_repository
from creativity_service.modules.iam.audit import append_event
from creativity_service.modules.iam.authorization import IamAuthorization
from creativity_service.modules.iam.repositories import policy_key
from creativity_service.modules.integrations.authorization import require_management
from creativity_service.modules.integrations.automation_schemas import (
    BatchCreate,
    BatchView,
    ItemView,
    ScheduleCreate,
    Toggle,
    next_window,
)
from creativity_service.modules.integrations.automation_tables import metadata
from creativity_service.modules.runs.services import RunService

KINDS = {
    "automation_schedules": "schedule",
    "automation_batches": "batch",
    "automation_items": "batch_item",
    "webhook_endpoints": "webhook_endpoint",
    "webhook_deliveries": "webhook_delivery",
}
LABELS = {
    "ACTIVE": "启用",
    "PAUSED": "暂停",
    "PENDING": "待派发",
    "CLAIMED": "派发中",
    "ADMITTED": "已受理",
    "FAILED": "受理失败",
    "CANCELLED": "已取消",
    "DELETED": "已删除",
}


def worker_identity(context: AuthContext) -> AuthContext:
    return context.model_copy(
        update={
            "principal_type": "worker",
            "session_id": None,
            "token_digest": None,
            "granted_actions": frozenset(),
        }
    )


def owner(context: AuthContext) -> str:
    return digest([context.actor_id, context.client_id, context.principal_id])


def scope_of(row: dict[str, Any]) -> Scope:
    return Scope.model_validate({k: row[k] for k in Scope.model_fields})


def repo(scope: Scope, name: str, *, include_deleted: bool = False) -> Repository:
    return Repository(metadata.tables[name], scope, include_deleted=include_deleted)


def keys(context: AuthContext, *records: tuple[str, str]) -> list[ResourceKey]:
    return [
        content_key(context.scope),
        policy_key(context.scope.channel_id),
        policy_key("system"),
        *(record_key(context.scope.channel_id, name, identifier) for name, identifier in records),
    ]


def configuration_keys(
    context: AuthContext, name: str, identifier: str, event_id: str
) -> list[ResourceKey]:
    return keys(context, (name, identifier), ("audit_events", event_id))


async def audit_configuration(
    uow: UnitOfWork, context: AuthContext, name: str, identifier: str, event_id: str
) -> None:
    await append_event(
        uow,
        event_id,
        context.actor_id or context.principal_id,
        context.request_id,
        "integration:configure",
        KINDS.get(name, "alert_rule"),
        identifier,
    )


async def add(
    uow: UnitOfWork, context: AuthContext, name: str, identifier: str, values: dict[str, Any]
) -> dict[str, Any]:
    protected = set(Scope.model_fields) | {
        "id",
        "created_at",
        "updated_at",
        "revision",
        "is_deleted",
    }
    return await repo(context.scope, name).add(
        uow,
        identifier,
        {**{c: None for c in metadata.tables[name].c.keys() if c not in protected}, **values},
    )


class AutomationService:
    def __init__(self, runs: RunService, authorization: IamAuthorization) -> None:
        self.runs, self.engine, self.authorization = runs, runs.engine, authorization

    async def manage(self, context: AuthContext) -> None:
        if not context.actor_id:
            raise ServiceError("FORBIDDEN", "此配置需要渠道管理身份", 403)
        await self.authorization.boundary(
            context, "integration:manage", "channel", context.scope.channel_id
        )

    async def get(self, context: AuthContext, name: str, identifier: str) -> dict[str, Any]:
        async with transaction(self.engine, context.scope, keys(context)) as uow:
            row = await repo(context.scope, name).get(uow.connection, identifier)
            if not row or row["owner_key"] != owner(context):
                raise ServiceError("NOT_FOUND", "当前身份与范围没有此记录", 404)
            await DeletionGuard(context.scope).check(uow, [ContentRef(KINDS[name], identifier)])
            return row

    async def channel_rows(self, channel_id: str, name: str) -> list[dict[str, Any]]:
        table = metadata.tables[name]
        async with self.engine.connect() as connection:
            return [
                dict(r)
                for r in (
                    await connection.execute(
                        active_rows(select(table).where(table.c.channel_id == channel_id))
                    )
                ).mappings()
            ]

    async def list_schedules(self, context: AuthContext) -> list[dict[str, Any]]:
        await self.manage(context)
        async with self.engine.connect() as connection:
            records = await repo(context.scope, "automation_schedules").find(
                connection, owner_key=owner(context)
            )
        return [self.schedule_view(r) for r in records if not r["is_deleted"]]

    @staticmethod
    def schedule_view(row: dict[str, Any]) -> dict[str, Any]:
        return {k: row[k] for k in ("id", "name", "spec", "revision", "next_at", "last_error")} | {
            "state": row["state"],
            "state_label": LABELS[row["state"]],
        }

    async def create_schedule(self, context: AuthContext, body: ScheduleCreate) -> dict[str, Any]:
        await self.manage(context)
        await self.runs.authorization.require(context, "run:create", "new")
        identifier = new_id("schedule")
        event_id = new_id("audit")
        async with transaction(
            self.engine,
            context.scope,
            configuration_keys(context, "automation_schedules", identifier, event_id),
        ) as uow:
            await require_management(uow, context, "integration:manage")
            await DeletionGuard(context.scope).check(uow, [])
            await self.require_scheduled_agent(uow, context, body.request.agent_code)
            row = await add(
                uow,
                context,
                "automation_schedules",
                identifier,
                {
                    "name": body.name,
                    "spec": body.model_dump(mode="json"),
                    "owner_key": owner(context),
                    "identity": worker_identity(context).model_dump(mode="json"),
                    "state": "ACTIVE",
                    "next_at": next_window(body, utcnow()),
                    "last_error": None,
                },
            )
            await audit_configuration(uow, context, "automation_schedules", identifier, event_id)
        return self.schedule_view(row)

    @staticmethod
    async def require_scheduled_agent(
        uow: UnitOfWork, context: AuthContext, agent_code: str
    ) -> None:
        """与计划保存共用渠道策略锁，排除未发布、停用和已删除的智能体。"""
        agents = await agent_repository("agents", context.scope).find(
            uow.connection, agent_code=agent_code
        )
        if len(agents) != 1:
            raise ServiceError("NOT_FOUND", "智能体不存在", 404)
        agent = agents[0]
        await locked_require(uow, context, "run:create", "agent", agent["id"])
        mapping_id = digest(
            [context.scope.channel_id, context.scope.environment, "agent", agent["id"]]
        )
        state = await agent_repository("agent_environment_states", context.scope).get(
            uow.connection, mapping_id
        )
        if agent["status"] != "ACTIVE" or (state and state["status"] != "ACTIVE"):
            raise ServiceError("AGENT_DISABLED", "智能体已下线或停用", 403)
        mapping = await agent_repository("release_mappings", context.scope).get(
            uow.connection, mapping_id
        )
        version = (
            await agent_repository("resource_versions", context.scope).get(
                uow.connection, mapping["version_id"]
            )
            if mapping
            else None
        )
        if not version or version["state"] != "PUBLISHED":
            raise ServiceError("AGENT_NOT_RELEASED", "智能体尚未发布到当前环境", 409)
        await DeletionGuard(context.scope).check(
            uow, [ContentRef("agent", agent["id"]), ContentRef("version", version["id"])]
        )

    async def toggle_schedule(
        self, context: AuthContext, identifier: str, body: Toggle
    ) -> dict[str, Any]:
        await self.manage(context)
        await self.get(context, "automation_schedules", identifier)
        event_id = new_id("audit")
        async with transaction(
            self.engine,
            context.scope,
            configuration_keys(context, "automation_schedules", identifier, event_id),
        ) as uow:
            await require_management(uow, context, "integration:manage")
            await DeletionGuard(context.scope).check(uow, [ContentRef("schedule", identifier)])
            row = await repo(context.scope, "automation_schedules").get(uow.connection, identifier)
            assert row
            values: dict[str, Any] = {"state": "ACTIVE" if body.active else "PAUSED"}
            if body.active:
                await self.require_scheduled_agent(
                    uow, context, row["spec"]["request"]["agent_code"]
                )
                values["next_at"] = next_window(
                    ScheduleCreate.model_validate(row["spec"]), utcnow()
                )
            row = await repo(context.scope, "automation_schedules").change(
                uow, identifier, body.revision, values
            )
            await audit_configuration(uow, context, "automation_schedules", identifier, event_id)
        return self.schedule_view(row)

    async def batch_views(
        self,
        uow: UnitOfWork,
        context: AuthContext,
        records: list[dict[str, Any]],
        *,
        skip_deleted: bool = False,
    ) -> list[BatchView]:
        # 批次历史保留已删除条目的占位，继续校验归属并隐藏运行及错误内容。
        items = await repo(context.scope, "automation_items", include_deleted=True).get_many(
            uow.connection, [identifier for r in records for identifier in r["item_ids"]]
        )
        refs = [ContentRef("batch", r["id"]) for r in records]
        refs.extend(ContentRef("batch_item", r["id"]) for r in items.values())
        refs.extend(ContentRef("run", r["run_id"]) for r in items.values() if r["run_id"])
        blocked = await DeletionGuard(context.scope).blocked_refs(uow, refs)
        result = []
        for row in records:
            if row["owner_key"] != owner(context):
                raise ServiceError("NOT_FOUND", "当前身份与范围没有此记录", 404)
            if ContentRef("batch", row["id"]) in blocked:
                if skip_deleted:
                    continue
                raise ServiceError("CONTENT_DELETED", "内容或来源已删除", 410)
            views = []
            for identifier in row["item_ids"]:
                item = items.get(identifier)
                if not item or item["owner_key"] != owner(context):
                    raise ServiceError("NOT_FOUND", "当前身份与范围没有此条目", 404)
                if (
                    item["is_deleted"]
                    or ContentRef("batch_item", identifier) in blocked
                    or (item["run_id"] and ContentRef("run", item["run_id"]) in blocked)
                ):
                    item = {
                        **item,
                        "is_deleted": True,
                        "state": "DELETED",
                        "run_id": None,
                        "error": None,
                    }
                views.append(self.item_view(item))
            result.append(
                BatchView(
                    batch_id=row["id"],
                    name=row["name"],
                    state_label=LABELS[row["state"]],
                    revision=row["revision"],
                    items=views,
                )
            )
        return result

    async def batch(self, context: AuthContext, identifier: str) -> BatchView:
        await self.runs.authorization.require(context, "run:read", "scope")
        async with transaction(self.engine, context.scope, keys(context)) as uow:
            row = await repo(context.scope, "automation_batches").get(uow.connection, identifier)
            if row is None:
                raise ServiceError("NOT_FOUND", "当前身份与范围没有此记录", 404)
            return (await self.batch_views(uow, context, [row]))[0]

    async def list_batches(self, context: AuthContext) -> list[BatchView]:
        from creativity_service.modules.iam.reading import require_action

        if not context.actor_id:
            raise ServiceError("FORBIDDEN", "此配置需要渠道管理身份", 403)
        policy = await self.authorization.read_policy(context)
        require_action(policy.actions("channel", context.scope.channel_id), "integration:manage")
        require_action(policy.actions("run", "scope"), "run:read")
        table = metadata.tables["automation_batches"]
        result: list[BatchView] = []
        after = None
        async with transaction(self.engine, context.scope, keys(context)) as uow:
            while len(result) < 100:
                from sqlalchemy import tuple_

                predicates = [
                    repo(context.scope, "automation_batches").predicate(),
                    table.c.owner_key == owner(context),
                    table.c.is_deleted.is_(False),
                ]
                if after:
                    predicates.append(tuple_(table.c.created_at, table.c.id) < after)
                records = [
                    dict(r)
                    for r in (
                        await uow.connection.execute(
                            active_rows(
                                select(table)
                                .where(*predicates)
                                .order_by(table.c.created_at.desc(), table.c.id.desc())
                                .limit(100 - len(result))
                            )
                        )
                    ).mappings()
                ]
                if not records:
                    break
                result.extend(await self.batch_views(uow, context, records, skip_deleted=True))
                after = (records[-1]["created_at"], records[-1]["id"])
        return result

    @staticmethod
    async def item_deleted(uow: UnitOfWork, context: AuthContext, row: dict[str, Any]) -> bool:
        refs = [ContentRef("batch_item", row["id"])]
        if row["run_id"]:
            # 运行删除标记先于派生清理任务，读取和取消均须立即屏蔽该条目。
            refs.append(ContentRef("run", row["run_id"]))
        try:
            await DeletionGuard(context.scope).check(uow, refs)
        except ServiceError as exc:
            if exc.code != "CONTENT_DELETED":
                raise
            return True
        return bool(row["is_deleted"])

    @staticmethod
    def item_view(row: dict[str, Any]) -> ItemView:
        return ItemView(
            item_id=row["id"],
            event_id=row["event_id"],
            state=row["state"],
            state_label=LABELS[row["state"]],
            run_id=row["run_id"],
            error=row["error"],
            revision=row["revision"],
        )

    async def create_batch(self, context: AuthContext, body: BatchCreate, key: str) -> BatchView:
        await self.runs.authorization.require(context, "run:create", "new")
        if not key or len(key) > 128:
            raise ServiceError("IDEMPOTENCY_KEY_REQUIRED", "请提供有效幂等键", 422)
        identifier = digest([context.scope.model_dump(), owner(context), "batch", key])
        items = [
            (digest([context.scope.model_dump(), owner(context), "event", i.event_id]), i)
            for i in body.items
        ]
        links = [(digest(["batch", identifier, item_id]), item_id) for item_id, _ in items]
        lock = keys(
            context,
            ("automation_batches", identifier),
            *(("automation_items", i) for i, _ in items),
            *(("source_links", i) for i, _ in links),
        )
        async with transaction(self.engine, context.scope, lock) as uow:
            await DeletionGuard(context.scope).check(uow, [ContentRef("batch", identifier)])
            previous = await repo(context.scope, "automation_batches").get(
                uow.connection, identifier
            )
            if previous and previous["request_digest"] != digest(body.model_dump(mode="json")):
                raise ServiceError("IDEMPOTENCY_CONFLICT", "相同批次幂等键的内容不同", 409)
            if not previous:
                await add(
                    uow,
                    context,
                    "automation_batches",
                    identifier,
                    {
                        "name": body.name,
                        "request_digest": digest(body.model_dump(mode="json")),
                        "item_ids": [i for i, _ in items],
                        "owner_key": owner(context),
                        "identity": worker_identity(context).model_dump(mode="json"),
                        "state": "ACTIVE",
                    },
                )
                for item_id, item in items:
                    semantic = item.request.semantic_digest()
                    previous_item = await repo(context.scope, "automation_items").get(
                        uow.connection, item_id
                    )
                    if previous_item and previous_item["request_digest"] != semantic:
                        raise ServiceError(
                            "IDEMPOTENCY_CONFLICT", "相同外部事件的运行内容不同", 409
                        )
                    if not previous_item:
                        await self.new_item(
                            uow, context, item_id, item.request, item.event_id, batch_id=identifier
                        )
                    await DeletionGuard(context.scope).link(
                        uow,
                        digest(["batch", identifier, item_id]),
                        ContentRef("batch", identifier),
                        ContentRef("batch_item", item_id),
                    )
        return await self.batch(context, identifier)

    async def new_item(
        self,
        uow: UnitOfWork,
        context: AuthContext,
        identifier: str,
        request: RunInput,
        event_id: str,
        *,
        batch_id: str | None = None,
        schedule_id: str | None = None,
    ) -> None:
        await DeletionGuard(context.scope).check(uow, [ContentRef("batch_item", identifier)])
        await add(
            uow,
            context,
            "automation_items",
            identifier,
            {
                "batch_id": batch_id,
                "schedule_id": schedule_id,
                "event_id": event_id,
                "request_digest": request.semantic_digest(),
                "request": request.model_dump(mode="json"),
                "owner_key": owner(context),
                "identity": worker_identity(context).model_dump(mode="json"),
                "state": "PENDING",
                "attempts": 0,
            },
        )

    async def retry_item(self, context: AuthContext, identifier: str, revision: int) -> ItemView:
        await self.runs.authorization.require(context, "run:create", "new")
        await self.get(context, "automation_items", identifier)
        async with transaction(
            self.engine, context.scope, keys(context, ("automation_items", identifier))
        ) as uow:
            await DeletionGuard(context.scope).check(uow, [ContentRef("batch_item", identifier)])
            row = await repo(context.scope, "automation_items").get(uow.connection, identifier)
            assert row
            if row["state"] != "FAILED" or row["run_id"]:
                raise ServiceError("ITEM_NOT_RETRYABLE", "只有尚未受理的失败条目可重试", 409)
            if row["batch_id"]:
                parent = await repo(context.scope, "automation_batches").get(
                    uow.connection, row["batch_id"]
                )
                if not parent or parent["state"] != "ACTIVE":
                    raise ServiceError("BATCH_CANCELLED", "批次已停止派发", 409)
            row = await repo(context.scope, "automation_items").change(
                uow,
                identifier,
                revision,
                {"state": "PENDING", "error": None, "lease_until": None, "lease_nonce": None},
            )
        return self.item_view(row)

    async def cancel_batch(
        self, context: AuthContext, identifier: str, revision: int, cancel_runs: bool
    ) -> BatchView:
        await self.runs.authorization.require(context, "run:create", "new")
        batch = await self.get(context, "automation_batches", identifier)
        run_ids = []
        async with transaction(
            self.engine,
            context.scope,
            keys(
                context,
                ("automation_batches", identifier),
                *(("automation_items", i) for i in batch["item_ids"]),
            ),
        ) as uow:
            await DeletionGuard(context.scope).check(uow, [ContentRef("batch", identifier)])
            await repo(context.scope, "automation_batches").change(
                uow, identifier, revision, {"state": "CANCELLED"}
            )
            for item_id in batch["item_ids"]:
                item = await repo(context.scope, "automation_items").get(uow.connection, item_id)
                if not item or item["batch_id"] != identifier:
                    continue
                if await self.item_deleted(uow, context, item):
                    continue
                if item["state"] in {"PENDING", "CLAIMED", "FAILED"}:
                    await repo(context.scope, "automation_items").change(
                        uow, item_id, item["revision"], {"state": "CANCELLED"}
                    )
                if item["run_id"] and cancel_runs:
                    run_ids.append(item["run_id"])
        for run_id in run_ids:
            try:
                await self.runs.cancel(context, run_id)
            except ServiceError as exc:
                # 批次事务提交后仍可能收到删除标记，不阻断其他条目的取消。
                if exc.code != "CONTENT_DELETED":
                    raise
        return await self.batch(context, identifier)

    async def fire(self, row: dict[str, Any], now: datetime) -> None:
        context = AuthContext.model_validate(row["identity"])
        spec = ScheduleCreate.model_validate(row["spec"])
        identifier = digest(["schedule", row["id"], row["next_at"].isoformat()])
        link = digest(["schedule", row["id"], identifier])
        async with transaction(
            self.engine,
            context.scope,
            keys(
                context,
                ("automation_schedules", row["id"]),
                ("automation_items", identifier),
                ("source_links", link),
            ),
        ) as uow:
            current = await repo(context.scope, "automation_schedules").get(
                uow.connection, row["id"]
            )
            if (
                not current
                or current["revision"] != row["revision"]
                or current["state"] != "ACTIVE"
                or current["next_at"] > now
            ):
                return
            await DeletionGuard(context.scope).check(uow, [ContentRef("schedule", row["id"])])
            next_at = next_window(spec, now)
            if spec.interval_seconds:
                missed = int((now - row["next_at"]).total_seconds() // spec.interval_seconds)
                next_at = row["next_at"] + timedelta(seconds=spec.interval_seconds * (missed + 1))
            await repo(context.scope, "automation_schedules").change(
                uow, row["id"], row["revision"], {"next_at": next_at}
            )
            if (now - row["next_at"]).total_seconds() > 60:
                return
            await self.new_item(
                uow,
                context,
                identifier,
                spec.request,
                row["next_at"].isoformat(),
                schedule_id=row["id"],
            )
            await DeletionGuard(context.scope).link(
                uow, link, ContentRef("schedule", row["id"]), ContentRef("batch_item", identifier)
            )

    async def dispatch(self, row: dict[str, Any]) -> None:
        context = AuthContext.model_validate(row["identity"])
        nonce = secrets.token_hex(16)
        item_repo = repo(context.scope, "automation_items")
        lock = keys(context, ("automation_items", row["id"]))
        async with transaction(self.engine, context.scope, lock) as uow:
            current = await item_repo.get(uow.connection, row["id"])
            if not current or current["state"] not in {"PENDING", "CLAIMED"}:
                return
            if current["lease_until"] and current["lease_until"] > utcnow():
                return
            await DeletionGuard(context.scope).check(uow, [ContentRef("batch_item", row["id"])])
            for table, field in (
                ("automation_batches", "batch_id"),
                ("automation_schedules", "schedule_id"),
            ):
                if current[field]:
                    parent = await repo(context.scope, table).get(uow.connection, current[field])
                    if not parent or parent["state"] != "ACTIVE":
                        return
            await item_repo.change(
                uow,
                row["id"],
                current["revision"],
                {
                    "state": "CLAIMED",
                    "lease_nonce": nonce,
                    "lease_until": utcnow() + timedelta(seconds=120),
                    "attempts": current["attempts"] + 1,
                },
            )
        receipt, error = None, None
        try:
            receipt = await self.runs.admit_run(
                context,
                RunInput.model_validate(row["request"]),
                "item:" + row["id"],
                sources=(ContentRef("batch_item", row["id"]),),
            )
        except ServiceError as exc:
            error = {"code": exc.code, "message": exc.message}
        cancel = False
        async with transaction(self.engine, context.scope, lock) as uow:
            current = await item_repo.get(uow.connection, row["id"])
            if not current or (current["is_deleted"] or current["state"] == "CANCELLED"):
                cancel = bool(receipt)
            elif current["lease_nonce"] != nonce:
                return
            else:
                await DeletionGuard(context.scope).check(uow, [ContentRef("batch_item", row["id"])])
                await item_repo.change(
                    uow,
                    row["id"],
                    current["revision"],
                    {
                        "state": "ADMITTED" if receipt else "FAILED",
                        "run_id": receipt.run_id if receipt else None,
                        "error": error,
                        "lease_nonce": None,
                        "lease_until": None,
                    },
                )
        if cancel and receipt:
            await self.runs.cancel(context, receipt.run_id)

    async def sweep(self, channel_id: str) -> None:
        now = utcnow()
        for row in await self.channel_rows(channel_id, "automation_schedules"):
            if row["state"] == "ACTIVE" and row["next_at"] <= now:
                try:
                    await self.fire(row, now)
                except ServiceError:
                    continue
        for row in await self.channel_rows(channel_id, "automation_items"):
            if row["state"] in {"PENDING", "CLAIMED"}:
                try:
                    await self.dispatch(row)
                except ServiceError:
                    continue
