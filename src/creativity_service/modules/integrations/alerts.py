"""复用现有预算提醒与运行事实，周期转换先持久化后交给事件投递。"""

from datetime import datetime, timedelta
from typing import Any, Literal

from pydantic import Field
from sqlalchemy import select

from creativity_service.core.context import AuthContext
from creativity_service.core.database import Repository, transaction
from creativity_service.core.deletion import ContentRef, DeletionGuard
from creativity_service.core.primitives import (
    Contract,
    Identifier,
    Revision,
    ServiceError,
    digest,
    new_id,
    utcnow,
)
from creativity_service.modules.data_lifecycle.tables import metadata as lifecycle_metadata
from creativity_service.modules.iam.operations_tables import metadata
from creativity_service.modules.integrations.authorization import require_management
from creativity_service.modules.integrations.automation import (
    AutomationService,
    audit_configuration,
    configuration_keys,
    keys,
    owner,
    worker_identity,
)
from creativity_service.modules.integrations.automation_tables import (
    metadata as integration_metadata,
)
from creativity_service.modules.integrations.webhooks import WebhookService
from creativity_service.modules.runs.tables import metadata as run_metadata
from creativity_service.modules.usage.tables import metadata as usage_metadata

KINDS = {
    "budget": "预算阈值",
    "run_failure": "运行失败",
    "cleanup_failure": "清理异常",
    "delivery_failure": "投递异常",
}


class AlertSave(Contract):
    revision: Revision | None = None
    name: str = Field(min_length=1, max_length=128)
    kind: Literal["budget", "run_failure", "cleanup_failure", "delivery_failure"]
    threshold: int = Field(default=1, ge=1, le=10000, strict=True)
    window_seconds: int = Field(default=3600, ge=60, le=604800, strict=True)
    endpoint_id: Identifier
    client_ids: list[Identifier] | None = Field(default=None, max_length=100)
    active: bool = True


class Alerts:
    def __init__(self, automation: AutomationService, webhooks: WebhookService) -> None:
        self.automation, self.webhooks, self.engine = automation, webhooks, automation.engine

    async def authorize(self, context: AuthContext, kind: str) -> None:
        await self.automation.manage(context)
        action = {
            "budget": "budget:manage",
            "run_failure": "run:read",
            "cleanup_failure": "content:delete",
            "delivery_failure": "integration:manage",
        }[kind]
        await self.automation.authorization.boundary(
            context, action, "channel", context.scope.channel_id
        )

    def repo(self, context: AuthContext) -> Repository:
        return Repository(metadata.tables["alert_rules"], context.scope)

    async def list(self, context: AuthContext) -> list[dict[str, Any]]:
        await self.automation.manage(context)
        async with self.engine.connect() as connection:
            rows = await self.repo(context).find(connection, owner_key=owner(context))
        return [
            {
                k: row[k]
                for k in (
                    "id",
                    "name",
                    "kind",
                    "threshold",
                    "window_seconds",
                    "endpoint_id",
                    "client_ids",
                    "revision",
                    "active",
                    "generation",
                    "last_value",
                )
            }
            | {
                "kind_label": KINDS[row["kind"]],
                "enabled": row["state"] == "ACTIVE",
                "state_label": "停用"
                if row["state"] != "ACTIVE"
                else "已触发"
                if row["active"]
                else "正常",
            }
            for row in rows
            if row["state"] != "DELETED"
        ]

    async def save(
        self, context: AuthContext, body: AlertSave, identifier: str | None = None
    ) -> dict[str, Any]:
        await self.authorize(context, body.kind)
        if body.client_ids and body.kind != "run_failure":
            raise ServiceError("SUBSCRIPTION_INVALID", "只有运行失败告警可选择调用服务", 422)
        endpoint = await self.automation.get(context, "webhook_endpoints", body.endpoint_id)
        if not {"alert.triggered", "alert.resolved"} <= set(endpoint["events"]):
            raise ServiceError("ALERT_ENDPOINT_INVALID", "端点须同时订阅告警触发与解除", 422)
        identifier = identifier or new_id("alert_rule")
        event_id = new_id("audit")
        async with transaction(
            self.engine,
            context.scope,
            configuration_keys(context, "alert_rules", identifier, event_id),
        ) as uow:
            await require_management(uow, context, "integration:manage")
            await DeletionGuard(context.scope).check(
                uow,
                [
                    ContentRef("alert_rule", identifier),
                    ContentRef("webhook_endpoint", body.endpoint_id),
                ],
            )
            current = await self.repo(context).get(uow.connection, identifier)
            if current and current["owner_key"] != owner(context):
                raise ServiceError("NOT_FOUND", "当前身份没有此告警规则", 404)
            if bool(current) != (body.revision is not None):
                raise ServiceError("REVISION_CONFLICT", "告警规则已经变化", 409)
            client_ids = (
                body.client_ids
                if body.client_ids is not None
                else current["client_ids"]
                if current
                else []
            )
            if not current or body.active or client_ids != current["client_ids"]:
                await self.webhooks.subscriptions.validate(uow, context, client_ids)
            values = {
                **body.model_dump(exclude={"revision", "active"}),
                "state": "ACTIVE" if body.active else "PAUSED",
                "client_ids": sorted(client_ids),
            }
            if current:
                if body.kind != current["kind"] or body.endpoint_id != current["endpoint_id"]:
                    raise ServiceError("IMMUTABLE_FIELD", "监测类型与投递端点须新建规则修改", 422)
                assert body.revision is not None
                if values["client_ids"] != current["client_ids"]:
                    # 统计范围变化后重新观测；旧范围的待投递记录在发送时再次拒绝。
                    values.update(active=False, last_value=0, pending_events=[])
                await self.repo(context).change(uow, identifier, body.revision, values)
            else:
                await self.repo(context).add(
                    uow,
                    identifier,
                    {
                        **values,
                        "owner_key": owner(context),
                        "identity": worker_identity(context).model_dump(mode="json"),
                        "active": False,
                        "generation": 0,
                        "last_value": 0,
                        "pending_events": [],
                    },
                )
            await audit_configuration(uow, context, "alert_rules", identifier, event_id)
        return next(row for row in await self.list(context) if row["id"] == identifier)

    async def count(self, context: AuthContext, row: dict[str, Any]) -> int:
        name = {
            "run_failure": "runs",
            "cleanup_failure": "deletion_work_items",
            "delivery_failure": "webhook_deliveries",
        }[row["kind"]]
        table = {**run_metadata.tables, **lifecycle_metadata.tables, **integration_metadata.tables}[
            name
        ]
        conditions = [
            self.webhooks.subscriptions.predicate(context, row["client_ids"])
            if name == "runs"
            else Repository(table, context.scope).predicate(),
            table.c.state == "FAILED",
            table.c.updated_at >= utcnow() - timedelta(seconds=row["window_seconds"]),
        ]
        if name == "webhook_deliveries":
            conditions += [table.c.owner_key == owner(context), table.c.kind == "run.terminal"]
        async with self.engine.connect() as connection:
            rows = [
                dict(v)
                for v in (await connection.execute(select(table).where(*conditions))).mappings()
            ]
        if name == "runs":
            count = 0
            for run in rows:
                try:
                    await self.webhooks.subscriptions.authorize(context, row["client_ids"], run)
                except ServiceError as exc:
                    if exc.status not in {401, 403, 404, 410}:
                        raise
                else:
                    count += 1
            return count
        return len(rows)

    async def budget_events(
        self, context: AuthContext, row: dict[str, Any], endpoint: dict[str, Any]
    ) -> None:
        async with self.engine.connect() as connection:
            table = usage_metadata.tables["budget_alerts"]
            alerts = [
                dict(r)
                for r in (
                    await connection.execute(
                        select(table).where(table.c.channel_id == context.scope.channel_id)
                    )
                ).mappings()
            ]
        for alert in alerts:
            for index, change in enumerate(alert["transitions"]):
                if datetime.fromisoformat(change["at"]) < row["created_at"]:
                    continue
                kind = "alert.triggered" if change["status"] == "ACTIVE" else "alert.resolved"
                event_id = digest([row["id"], alert["id"], index, kind])
                await self.webhooks.enqueue(
                    context,
                    endpoint,
                    event_id,
                    kind,
                    {
                        "alert_rule_id": row["id"],
                        "name": row["name"],
                        "category": "budget",
                        "period_start": alert["period_start"].isoformat(),
                        "threshold": str(alert["threshold"]),
                        "observed_at": change["at"],
                        "generation": index + 1,
                    },
                    (ContentRef("alert_rule", row["id"]),),
                )

    async def observe(self, row: dict[str, Any]) -> None:
        context = AuthContext.model_validate(row["identity"])
        await self.authorize(context, row["kind"])
        endpoint = await self.automation.get(context, "webhook_endpoints", row["endpoint_id"])
        if row["kind"] == "budget":
            await self.budget_events(context, row, endpoint)
            return
        await self.webhooks.subscriptions.require_clients(context, row["client_ids"])
        value = await self.count(context, row)
        async with transaction(
            self.engine, context.scope, keys(context, ("alert_rules", row["id"]))
        ) as uow:
            current = await self.repo(context).get(uow.connection, row["id"])
            if (
                not current
                or current["state"] != "ACTIVE"
                or current["revision"] != row["revision"]
            ):
                return
            await DeletionGuard(context.scope).check(uow, [ContentRef("alert_rule", row["id"])])
            active = value >= current["threshold"]
            pending = current["pending_events"]
            generation = current["generation"] + int(active and not current["active"])
            if active != current["active"]:
                pending = [
                    *pending,
                    {
                        "kind": "alert.triggered" if active else "alert.resolved",
                        "generation": generation,
                        "observed_at": utcnow().isoformat(),
                        "value": value,
                    },
                ]
            if len(pending) > 100:
                raise ServiceError("ALERT_BACKLOG_LIMIT", "告警事件积压，请先处理投递端点", 503)
            current = await self.repo(context).change(
                uow,
                row["id"],
                current["revision"],
                {
                    "active": active,
                    "generation": generation,
                    "last_value": value,
                    "pending_events": pending,
                },
            )
        accepted = True
        for event in current["pending_events"]:
            queued = await self.webhooks.enqueue(
                context,
                endpoint,
                digest([row["id"], event["generation"], event["kind"]]),
                event["kind"],
                {
                    "alert_rule_id": row["id"],
                    "name": row["name"],
                    "category": row["kind"],
                    "client_ids": row["client_ids"],
                    **{k: v for k, v in event.items() if k != "kind"},
                },
                (ContentRef("alert_rule", row["id"]),),
            )
            accepted = accepted and queued
        if accepted and current["pending_events"]:
            async with transaction(
                self.engine, context.scope, keys(context, ("alert_rules", row["id"]))
            ) as uow:
                latest = await self.repo(context).get(uow.connection, row["id"])
                if latest and latest["revision"] == current["revision"]:
                    await self.repo(context).change(
                        uow, row["id"], latest["revision"], {"pending_events": []}
                    )

    async def sweep(self, channel_id: str) -> None:
        table = metadata.tables["alert_rules"]
        async with self.engine.connect() as connection:
            rows = [
                dict(r)
                for r in (
                    await connection.execute(
                        select(table).where(
                            table.c.channel_id == channel_id, table.c.state == "ACTIVE"
                        )
                    )
                ).mappings()
            ]
        for row in rows:
            try:
                await self.observe(row)
            except ServiceError:
                continue
