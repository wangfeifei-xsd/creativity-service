"""定时与批量条目先持久化，再以稳定幂等键进入统一运行。"""

import secrets
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import and_, func, select, tuple_

from creativity_service.core.context import AuthContext, Scope
from creativity_service.core.database import Repository, UnitOfWork, transaction
from creativity_service.core.database.soft_delete import active_rows
from creativity_service.core.deletion import ContentRef, DeletionGuard, content_key
from creativity_service.core.locking import ResourceKey, record_key
from creativity_service.core.primitives import RunInput, ServiceError, digest, new_id, utcnow
from creativity_service.core.schema_validation import SchemaInputError, schema_errors
from creativity_service.modules.agents.access import locked_require
from creativity_service.modules.agents.deletion import require_undeleted_agents
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
    ScheduleTodo,
    ScheduleTodoList,
    ScheduleUpdate,
    Toggle,
    next_window,
)
from creativity_service.modules.integrations.automation_tables import metadata
from creativity_service.modules.runs.services import RunService
from creativity_service.modules.runs.tables import metadata as run_metadata

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

    async def list_schedules(
        self, context: AuthContext, identifier: str | None = None
    ) -> list[dict[str, Any]]:
        await self.manage(context)
        async with self.engine.connect() as connection:
            records = await repo(context.scope, "automation_schedules").find(
                connection, owner_key=owner(context), **({"id": identifier} if identifier else {})
            )
        return [self.schedule_view(r) for r in records if not r["is_deleted"]]

    async def schedule_todos(
        self, context: AuthContext, page: int = 1, page_size: int = 5
    ) -> ScheduleTodoList:
        await self.manage(context)
        if page < 1 or not 1 <= page_size <= 100:
            raise ServiceError("INVALID_INPUT", "待办分页参数不正确", 422)
        table = metadata.tables["automation_schedules"]
        # 待办直接来自计划当前状态，恢复后自动消失；沿用计划的主体及所有者范围。
        predicate = and_(
            repo(context.scope, "automation_schedules").predicate(),
            table.c.is_deleted.is_(False),
            table.c.owner_key == owner(context),
            table.c.state == "PAUSED",
            table.c.last_error["code"].as_string() == "SCHEDULE_FAILURE_LIMIT",
        )
        async with self.engine.connect() as connection:
            total = await connection.scalar(
                select(func.count()).select_from(table).where(predicate)
            )
            records = (
                (
                    await connection.execute(
                        select(
                            table.c.id,
                            table.c.name,
                            table.c.consecutive_failures,
                            table.c.spec["failure_threshold"]
                            .as_integer()
                            .label("failure_threshold"),
                            table.c.last_error,
                        )
                        .where(predicate)
                        .order_by(table.c.updated_at.desc(), table.c.id)
                        .offset((page - 1) * page_size)
                        .limit(page_size)
                    )
                )
                .mappings()
                .all()
            )
        return ScheduleTodoList(
            total=total or 0,
            items=[
                ScheduleTodo(
                    id=row["id"],
                    name=row["name"],
                    consecutive_failures=row["consecutive_failures"],
                    failure_threshold=row["failure_threshold"] or 3,
                    message=row["last_error"].get("message") or "连续失败，已自动暂停",
                    run_id=row["last_error"].get("run_id"),
                )
                for row in records
            ],
        )

    @staticmethod
    def schedule_view(row: dict[str, Any]) -> dict[str, Any]:
        return {k: row[k] for k in ("id", "name", "spec", "revision", "next_at", "last_error")} | {
            "spec": ScheduleCreate.model_validate(row["spec"]).model_dump(mode="json"),
            "consecutive_failures": row["consecutive_failures"],
            "state": row["state"],
            "state_label": "失败自动暂停"
            if row["state"] == "PAUSED"
            and (row["last_error"] or {}).get("code") == "SCHEDULE_FAILURE_LIMIT"
            else LABELS[row["state"]],
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
            await self.require_scheduled_agent(uow, context, body.request)
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
                    "consecutive_failures": 0,
                    "outcome_cursor": {"created_at": utcnow().isoformat(), "id": ""},
                },
            )
            await audit_configuration(uow, context, "automation_schedules", identifier, event_id)
        return self.schedule_view(row)

    async def update_schedule(
        self, context: AuthContext, identifier: str, body: ScheduleUpdate
    ) -> dict[str, Any]:
        await self.manage(context)
        event_id = new_id("audit")
        spec = ScheduleCreate.model_validate(body.model_dump(exclude={"revision"}))
        async with transaction(
            self.engine,
            context.scope,
            configuration_keys(context, "automation_schedules", identifier, event_id),
        ) as uow:
            await require_management(uow, context, "integration:manage")
            row = await repo(context.scope, "automation_schedules").get(uow.connection, identifier)
            if not row or row["owner_key"] != owner(context):
                raise ServiceError("NOT_FOUND", "当前身份与范围没有此记录", 404)
            await DeletionGuard(context.scope).check(uow, [ContentRef("schedule", identifier)])
            await self.require_scheduled_agent(uow, context, spec.request)
            values: dict[str, Any] = {
                "name": spec.name,
                "spec": spec.model_dump(mode="json"),
            }
            # 周期改变才重排未来窗口；编辑名称或输入不推迟原有触发时间。
            # 与派发共用计划锁和修订检查，已生成的条目继续使用原始输入快照。
            if any(
                row["spec"].get(field) != values["spec"][field]
                for field in ("timezone", "interval_seconds", "daily_at")
            ):
                values["next_at"] = next_window(spec, utcnow())
            row = await repo(context.scope, "automation_schedules").change(
                uow, identifier, body.revision, values
            )
            row, _ = await self.settle_schedule(uow, context, row)
            await audit_configuration(uow, context, "automation_schedules", identifier, event_id)
        return self.schedule_view(row)

    @staticmethod
    async def require_scheduled_agent(
        uow: UnitOfWork, context: AuthContext, request: RunInput
    ) -> None:
        """在策略锁内校验当前上下线、启停状态及输入，避免保存或启用无效计划。"""
        agents = await agent_repository("agents", context.scope).find(
            uow.connection, agent_code=request.agent_code
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
            raise ServiceError("AGENT_DISABLED", "智能体已停用", 403)
        if not state or not state["is_online"]:
            raise ServiceError("AGENT_OFFLINE", "智能体尚未上线到当前环境", 409)
        version = await agent_repository("resource_versions", context.scope).get(
            uow.connection, agent["id"]
        )
        if not version or version["state"] != "CURRENT":
            raise ServiceError("AGENT_CONFIGURATION_MISSING", "智能体当前配置不可用", 409)
        await DeletionGuard(context.scope).check(
            uow, [ContentRef("agent", agent["id"]), ContentRef("version", version["id"])]
        )
        schema = version["content"]["input_schema"]
        fields = schema_errors(request.input, schema)
        if fields:
            for field in fields:
                name = field["path"][0] if field["path"] else None
                label = schema.get("properties", {}).get(name, {}).get("title") or "运行输入"
                field["message"] = f"{label}：{field['message']}"
                field["path"] = ["request", "input", *field["path"]]
            raise SchemaInputError("INPUT_SCHEMA_INVALID", "运行输入不符合当前智能体要求", fields)

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
            if body.active and row["state"] != "ACTIVE":
                await self.require_scheduled_agent(
                    uow, context, RunInput.model_validate(row["spec"]["request"])
                )
                values["next_at"] = next_window(
                    ScheduleCreate.model_validate(row["spec"]), utcnow()
                )
                values.update(
                    consecutive_failures=0,
                    last_error=None,
                    outcome_cursor={"created_at": utcnow().isoformat(), "id": ""},
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
                await require_undeleted_agents(
                    uow, context, [item.request.agent_code for _, item in items]
                )
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
            await require_undeleted_agents(uow, context, [row["request"]["agent_code"]])
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

    async def settle_schedule(
        self, uow: UnitOfWork, context: AuthContext, row: dict[str, Any]
    ) -> tuple[dict[str, Any], bool]:
        """在计划写锁内按触发顺序消费终态；游标使重启、重复扫描不会重复计数。"""
        if row["state"] != "ACTIVE":
            return row, False
        items, runs = metadata.tables["automation_items"], run_metadata.tables["runs"]
        cursor = row["outcome_cursor"]
        # 一次关联读取有界结果；运行记录按完整范围关联，已删除内容不参与诊断。
        records = [
            dict(r)
            for r in (
                await uow.connection.execute(
                    select(
                        items.c.id,
                        items.c.created_at,
                        items.c.state,
                        items.c.error,
                        items.c.run_id,
                        items.c.is_deleted,
                        runs.c.state.label("run_state"),
                        runs.c.error.label("run_error"),
                        runs.c.is_deleted.label("run_deleted"),
                    )
                    .select_from(
                        items.outerjoin(
                            runs,
                            and_(
                                runs.c.channel_id == items.c.channel_id,
                                runs.c.environment == items.c.environment,
                                runs.c.subject_type.is_not_distinct_from(items.c.subject_type),
                                runs.c.subject_id.is_not_distinct_from(items.c.subject_id),
                                runs.c.id == items.c.run_id,
                            ),
                        )
                    )
                    .where(
                        repo(context.scope, "automation_items", include_deleted=True).predicate(),
                        items.c.schedule_id == row["id"],
                        tuple_(items.c.created_at, items.c.id)
                        > (datetime.fromisoformat(cursor["created_at"]), cursor["id"]),
                    )
                    .order_by(items.c.created_at, items.c.id)
                    .limit(100)
                )
            ).mappings()
        ]
        count = row["consecutive_failures"]
        error = row["last_error"]
        threshold = ScheduleCreate.model_validate(row["spec"]).failure_threshold
        blocked = await DeletionGuard(context.scope).blocked_refs(
            uow,
            [ContentRef("batch_item", item["id"]) for item in records]
            + [ContentRef("run", item["run_id"]) for item in records if item["run_id"]],
        )
        for item in records:
            state = item["run_state"] if item["state"] == "ADMITTED" else item["state"]
            deleted = (
                item["is_deleted"]
                or item["run_deleted"]
                or ContentRef("batch_item", item["id"]) in blocked
                or (item["run_id"] and ContentRef("run", item["run_id"]) in blocked)
            )
            if not deleted and state not in {"FAILED", "TIMED_OUT", "SUCCEEDED", "CANCELLED"}:
                break
            cursor = {"created_at": item["created_at"].isoformat(), "id": item["id"]}
            if deleted or state == "CANCELLED":
                continue
            if state == "SUCCEEDED":
                count, error = 0, None
            else:
                count += 1
                error = {
                    **(
                        item["run_error"]
                        or item["error"]
                        or {
                            "code": "SCHEDULE_RUN_FAILED",
                            "message": "定时运行超时" if state == "TIMED_OUT" else "定时运行失败",
                        }
                    ),
                    "run_id": item["run_id"],
                }
            if count >= threshold:
                break
        values: dict[str, Any] = {
            "consecutive_failures": count,
            "outcome_cursor": cursor,
            "last_error": error,
        }
        if count >= threshold:
            reason = (error or {}).get("message", "未提供")
            values.update(
                state="PAUSED",
                last_error={
                    "code": "SCHEDULE_FAILURE_LIMIT",
                    "message": f"连续失败 {count} 次，已自动暂停。最近原因：{reason}",
                    "run_id": (error or {}).get("run_id"),
                },
            )
        if any(row.get(k) != v for k, v in values.items()):
            row = await repo(context.scope, "automation_schedules").change(
                uow, row["id"], row["revision"], values
            )
        # 积压结果未处理完时先推进游标，下一轮扫描再决定是否触发新任务。
        backlog = len(records) == 100 and cursor["id"] == records[-1]["id"]
        return row, backlog

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
            ):
                return
            await DeletionGuard(context.scope).check(uow, [ContentRef("schedule", row["id"])])
            current, backlog = await self.settle_schedule(uow, context, current)
            if current["state"] != "ACTIVE" or backlog or current["next_at"] > now:
                return
            next_at = next_window(spec, now)
            if spec.interval_seconds:
                missed = int((now - row["next_at"]).total_seconds() // spec.interval_seconds)
                next_at = row["next_at"] + timedelta(seconds=spec.interval_seconds * (missed + 1))
            await repo(context.scope, "automation_schedules").change(
                uow, row["id"], current["revision"], {"next_at": next_at}
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
        lock = keys(
            context,
            ("automation_items", row["id"]),
            *([("automation_schedules", row["schedule_id"])] if row["schedule_id"] else []),
        )
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
                    if field == "schedule_id":
                        parent, backlog = await self.settle_schedule(uow, context, parent)
                        if parent["state"] != "ACTIVE" or backlog:
                            return
                        cursor = parent["outcome_cursor"]
                        if (current["created_at"], current["id"]) <= (
                            datetime.fromisoformat(cursor["created_at"]),
                            cursor["id"],
                        ):
                            # 重新启用只接收未来窗口，暂停前未派发的条目不补跑。
                            await item_repo.change(
                                uow, current["id"], current["revision"], {"state": "CANCELLED"}
                            )
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
        for name, states in (
            ("automation_schedules", ["ACTIVE"]),
            ("automation_items", ["PENDING", "CLAIMED"]),
        ):
            table = metadata.tables[name]
            after = ""
            while True:
                async with self.engine.connect() as connection:
                    records = [
                        dict(r)
                        for r in (
                            await connection.execute(
                                active_rows(
                                    select(table)
                                    .where(
                                        table.c.channel_id == channel_id,
                                        table.c.state.in_(states),
                                        table.c.id > after,
                                    )
                                    .order_by(table.c.id)
                                    .limit(100)
                                )
                            )
                        ).mappings()
                    ]
                if not records:
                    break
                for row in records:
                    try:
                        if name == "automation_schedules":
                            await self.fire(row, now)
                        else:
                            await self.dispatch(row)
                    except ServiceError:
                        continue
                after = records[-1]["id"]
