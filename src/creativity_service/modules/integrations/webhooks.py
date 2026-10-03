"""最小事件、固定目的地址与签名密钥引用；投递在短事务之外执行。"""

import hashlib
import hmac
import secrets
from datetime import timedelta
from typing import Any

from pydantic import SecretBytes
from sqlalchemy import select

from creativity_service.core.context import AuthContext, TaskEnvelope
from creativity_service.core.database import Repository, transaction
from creativity_service.core.deletion import ContentRef, DeletionGuard
from creativity_service.core.primitives import ServiceError, canonical_json, digest, new_id, utcnow
from creativity_service.core.security.credentials import CredentialService, KeyProvider
from creativity_service.core.security.outbound import OutboundPolicy
from creativity_service.integrations.outbound import BoundedHttp
from creativity_service.modules.integrations.authorization import require_management
from creativity_service.modules.integrations.automation import (
    AutomationService,
    add,
    audit_configuration,
    configuration_keys,
    keys,
    owner,
    repo,
    worker_identity,
)
from creativity_service.modules.integrations.automation_schemas import WebhookCreate, WebhookUpdate
from creativity_service.modules.integrations.run_subscriptions import RunSubscriptions
from creativity_service.modules.runs.schemas import TERMINAL
from creativity_service.modules.runs.tables import metadata as runs_metadata

MAX_DELIVERY_ATTEMPTS = 6


class SigningAuthorization:
    def __init__(self, service: AutomationService, reference: str | None = None) -> None:
        self.service, self.reference = service, reference

    async def require(self, context: AuthContext, action: str, resource_id: str) -> None:
        if not (
            (action == "credential:write" and resource_id == "webhook")
            or (action == "credential:use" and resource_id == self.reference)
        ):
            raise ServiceError("FORBIDDEN", "签名密钥仅供当前投递使用", 403)
        await self.service.manage(context)


class WebhookService:
    def __init__(
        self,
        automation: AutomationService,
        policy: OutboundPolicy,
        provider: KeyProvider | None = None,
        http: BoundedHttp | None = None,
    ) -> None:
        self.automation, self.engine, self.policy, self.provider = (
            automation,
            automation.engine,
            policy,
            provider,
        )
        self.http = http or BoundedHttp(policy)
        self.subscriptions = RunSubscriptions(automation)

    def credentials(self, reference: str | None = None) -> CredentialService:
        return CredentialService(
            self.engine, self.provider, SigningAuthorization(self.automation, reference)
        )

    @staticmethod
    def endpoint_view(row: dict[str, Any]) -> dict[str, Any]:
        return {
            k: row[k] for k in ("id", "name", "url", "events", "revision", "state", "client_ids")
        } | {"state_label": "启用" if row["state"] == "ACTIVE" else "停用"}

    async def endpoints(self, context: AuthContext) -> list[dict[str, Any]]:
        await self.automation.manage(context)
        async with self.engine.connect() as connection:
            values = await repo(context.scope, "webhook_endpoints").find(
                connection, owner_key=owner(context)
            )
        return [self.endpoint_view(r) for r in values if r["state"] != "DELETED"]

    async def create(self, context: AuthContext, body: WebhookCreate) -> dict[str, Any]:
        await self.automation.manage(context)
        await self.policy.validate(context.scope, "webhook", body.url)
        identifier = new_id("webhook")
        reference = await self.credentials().store(
            context, "webhook", SecretBytes(body.secret.get_secret_value().encode())
        )
        event_id = new_id("audit")
        async with transaction(
            self.engine,
            context.scope,
            configuration_keys(context, "webhook_endpoints", identifier, event_id),
        ) as uow:
            await require_management(uow, context, "integration:manage")
            await self.subscriptions.validate(uow, context, body.client_ids)
            await DeletionGuard(context.scope).check(uow, [])
            row = await add(
                uow,
                context,
                "webhook_endpoints",
                identifier,
                {
                    "name": body.name,
                    "url": body.url,
                    "secret_ref": reference,
                    "events": list(body.events),
                    "client_ids": sorted(body.client_ids),
                    "state": "ACTIVE",
                    "owner_key": owner(context),
                    "identity": worker_identity(context).model_dump(mode="json"),
                },
            )
            await audit_configuration(uow, context, "webhook_endpoints", identifier, event_id)
        return self.endpoint_view(row)

    async def toggle(
        self, context: AuthContext, identifier: str, body: WebhookUpdate
    ) -> dict[str, Any]:
        await self.automation.manage(context)
        await self.automation.get(context, "webhook_endpoints", identifier)
        event_id = new_id("audit")
        async with transaction(
            self.engine,
            context.scope,
            configuration_keys(context, "webhook_endpoints", identifier, event_id),
        ) as uow:
            await require_management(uow, context, "integration:manage")
            await DeletionGuard(context.scope).check(
                uow, [ContentRef("webhook_endpoint", identifier)]
            )
            values: dict[str, Any] = {"state": "ACTIVE" if body.active else "PAUSED"}
            current = await repo(context.scope, "webhook_endpoints").get(uow.connection, identifier)
            if not current:
                raise ServiceError("NOT_FOUND", "投递端点不存在", 404)
            client_ids = getattr(body, "client_ids", None)
            client_ids = current["client_ids"] if client_ids is None else sorted(client_ids)
            if body.active or client_ids != current["client_ids"]:
                await self.subscriptions.validate(uow, context, client_ids)
            values["client_ids"] = client_ids
            row = await repo(context.scope, "webhook_endpoints").change(
                uow, identifier, body.revision, values
            )
            await audit_configuration(uow, context, "webhook_endpoints", identifier, event_id)
        return self.endpoint_view(row)

    async def deliveries(self, context: AuthContext) -> list[dict[str, Any]]:
        await self.automation.manage(context)
        async with self.engine.connect() as connection:
            records = await repo(context.scope, "webhook_deliveries").find(
                connection, owner_key=owner(context)
            )
            endpoints = await repo(context.scope, "webhook_endpoints").find(
                connection, owner_key=owner(context)
            )
        names = {r["id"]: r["name"] for r in endpoints}
        labels = {
            "PENDING": "待投递",
            "SENDING": "投递中",
            "RETRY": "等待重试",
            "SUCCEEDED": "已送达",
            "FAILED": "投递失败",
            "CANCELLED": "已停止",
            "DELETED": "已删除",
        }
        return [
            {
                k: r[k]
                for k in (
                    "id",
                    "event_id",
                    "kind",
                    "state",
                    "attempts",
                    "next_at",
                    "error",
                    "http_status",
                    "revision",
                )
            }
            | {"endpoint_name": names.get(r["endpoint_id"]), "state_label": labels[r["state"]]}
            for r in sorted(records, key=lambda r: r["created_at"], reverse=True)[:500]
            if r["state"] != "DELETED"
        ]

    async def retry(self, context: AuthContext, identifier: str, revision: int) -> None:
        await self.automation.manage(context)
        row = await self.automation.get(context, "webhook_deliveries", identifier)
        endpoint = await self.automation.get(context, "webhook_endpoints", row["endpoint_id"])
        if endpoint["state"] != "ACTIVE":
            raise ServiceError("ENDPOINT_DISABLED", "请先启用投递端点", 409)
        event_id = new_id("audit")
        async with transaction(
            self.engine,
            context.scope,
            configuration_keys(context, "webhook_deliveries", identifier, event_id),
        ) as uow:
            await require_management(uow, context, "integration:manage")
            await DeletionGuard(context.scope).check(
                uow, [ContentRef("webhook_delivery", identifier)]
            )
            current = await repo(context.scope, "webhook_deliveries").get(
                uow.connection, identifier
            )
            if not current or current["state"] not in {"FAILED", "CANCELLED"}:
                raise ServiceError("DELIVERY_NOT_RETRYABLE", "当前投递不能重试", 409)
            await repo(context.scope, "webhook_deliveries").change(
                uow,
                identifier,
                revision,
                {
                    "state": "PENDING",
                    "cycle_attempts": 0,
                    "next_at": utcnow(),
                    "lease_nonce": None,
                    "lease_until": None,
                },
            )
            await audit_configuration(uow, context, "webhook_deliveries", identifier, event_id)

    async def enqueue(
        self,
        context: AuthContext,
        endpoint: dict[str, Any],
        event_id: str,
        kind: str,
        payload: dict[str, Any],
        sources: tuple[ContentRef, ...] = (),
    ) -> bool:
        if kind not in endpoint["events"] or endpoint["state"] != "ACTIVE":
            return False
        identifier = digest([endpoint["id"], event_id])
        refs = (ContentRef("webhook_endpoint", endpoint["id"]), *sources)
        links = [(digest([identifier, s.resource_type, s.resource_id]), s) for s in refs]
        async with transaction(
            self.engine,
            context.scope,
            keys(
                context,
                ("webhook_deliveries", identifier),
                ("webhook_endpoints", endpoint["id"]),
                *(("source_links", i) for i, _ in links),
            ),
        ) as uow:
            await DeletionGuard(context.scope).check(uow, list(refs))
            actual = await repo(context.scope, "webhook_endpoints").get(
                uow.connection, endpoint["id"]
            )
            if (
                not actual
                or actual["state"] != "ACTIVE"
                or actual["revision"] != endpoint["revision"]
                or kind not in actual["events"]
            ):
                return False
            if await repo(context.scope, "webhook_deliveries").get(uow.connection, identifier):
                return True
            await add(
                uow,
                context,
                "webhook_deliveries",
                identifier,
                {
                    "endpoint_id": endpoint["id"],
                    "event_id": event_id,
                    "kind": kind,
                    "payload": {"event_id": event_id, "type": kind, **payload},
                    "owner_key": endpoint["owner_key"],
                    "state": "PENDING",
                    "attempts": 0,
                    "cycle_attempts": 0,
                    "next_at": utcnow(),
                },
            )
            for link, source in links:
                await DeletionGuard(context.scope).link(
                    uow, link, source, ContentRef("webhook_delivery", identifier)
                )
        return True

    async def terminal_events(self, endpoint: dict[str, Any]) -> None:
        context = AuthContext.model_validate(endpoint["identity"])
        await self.automation.manage(context)
        async with self.engine.connect() as connection:
            table = runs_metadata.tables["runs"]
            rows = [
                dict(r)
                for r in (
                    await connection.execute(
                        select(table).where(
                            self.subscriptions.predicate(context, endpoint["client_ids"]),
                            table.c.state.in_(TERMINAL),
                            table.c.updated_at >= endpoint["created_at"],
                        )
                    )
                ).mappings()
            ]
        for row in rows:
            try:
                await self.subscriptions.authorize(context, endpoint["client_ids"], row)
                await self.enqueue(
                    context,
                    endpoint,
                    digest(["run.terminal", row["id"], row["state"]]),
                    "run.terminal",
                    {
                        "run_id": row["id"],
                        "state": row["state"],
                        "occurred_at": row["updated_at"].isoformat(),
                        "status_path": ("/api/v1/runs/" if row["client_id"] else "/admin/v1/runs/")
                        + row["id"],
                    },
                    (ContentRef("run", row["id"]),),
                )
            except ServiceError:
                continue

    async def send(self, row: dict[str, Any], endpoint: dict[str, Any]) -> None:
        context = AuthContext.model_validate(endpoint["identity"])
        nonce = secrets.token_hex(16)
        delivery_repo = repo(context.scope, "webhook_deliveries")
        lock = keys(context, ("webhook_deliveries", row["id"]))
        async with transaction(self.engine, context.scope, lock) as uow:
            current = await delivery_repo.get(uow.connection, row["id"])
            if (
                not current
                or current["state"] not in {"PENDING", "RETRY", "SENDING"}
                or current["next_at"] > utcnow()
            ):
                return
            if current["lease_until"] and current["lease_until"] > utcnow():
                return
            await DeletionGuard(context.scope).check(
                uow, [ContentRef("webhook_delivery", row["id"])]
            )
            if current["cycle_attempts"] >= MAX_DELIVERY_ATTEMPTS:
                # 发出请求后 Worker 可能中断；租约恢复仍消耗原次数，不能额外发送第七次。
                await delivery_repo.change(
                    uow,
                    row["id"],
                    current["revision"],
                    {
                        "state": "FAILED",
                        "http_status": None,
                        "error": {
                            "code": "DELIVERY_ATTEMPTS_EXHAUSTED",
                            "message": "自动投递次数已达上限，最后一次结果未确认，可人工重投",
                        },
                        "lease_nonce": None,
                        "lease_until": None,
                    },
                )
                return
            current = await delivery_repo.change(
                uow,
                row["id"],
                current["revision"],
                {
                    "state": "SENDING",
                    "attempts": current["attempts"] + 1,
                    "cycle_attempts": current["cycle_attempts"] + 1,
                    "lease_nonce": nonce,
                    "lease_until": utcnow() + timedelta(seconds=60),
                },
            )
        status, error, state = None, None, "RETRY"
        try:

            async def deliver(secret: SecretBytes) -> int:
                await self.automation.manage(context)
                active = await self.automation.get(context, "webhook_endpoints", endpoint["id"])
                if active["state"] != "ACTIVE" or active["revision"] != endpoint["revision"]:
                    raise ServiceError("ENDPOINT_DISABLED", "投递端点已停用或变化", 403)
                latest = await self.automation.get(context, "webhook_deliveries", row["id"])
                if latest["lease_nonce"] != nonce:
                    raise ServiceError("DELIVERY_LEASE_LOST", "投递租约已变化", 409)
                if rule_id := current["payload"].get("alert_rule_id"):
                    from creativity_service.modules.iam.operations_tables import (
                        metadata as operations_metadata,
                    )

                    async with self.engine.connect() as connection:
                        rule = await Repository(
                            operations_metadata.tables["alert_rules"], context.scope
                        ).get(connection, rule_id)
                    if not rule or rule["state"] != "ACTIVE":
                        raise ServiceError("ALERT_DISABLED", "告警规则已停用", 403)
                    from creativity_service.modules.integrations.alerts import Alerts

                    await Alerts(self.automation, self).authorize(context, rule["kind"])
                    if rule["kind"] == "run_failure":
                        if rule["client_ids"] != current["payload"].get("client_ids", []):
                            raise ServiceError("ALERT_SCOPE_CHANGED", "告警监测范围已变化", 403)
                        await self.subscriptions.require_clients(context, rule["client_ids"])
                if run_id := current["payload"].get("run_id"):
                    run = await self.automation.runs.load(
                        TaskEnvelope(channel_id=context.scope.channel_id, run_id=run_id)
                    )
                    await self.subscriptions.authorize(context, active["client_ids"], run)
                body = canonical_json(current["payload"])
                timestamp = str(int(utcnow().timestamp()))
                signature = hmac.new(
                    secret.get_secret_value(), timestamp.encode() + b"." + body, hashlib.sha256
                ).hexdigest()
                response = await self.http.post(
                    context.scope,
                    "webhook",
                    endpoint["url"],
                    body,
                    headers={
                        "X-Creativity-Event-Id": current["event_id"],
                        "X-Creativity-Timestamp": timestamp,
                        "X-Creativity-Signature": "v1=" + signature,
                    },
                )
                return response.status

            status = await self.credentials(endpoint["secret_ref"]).call(
                context, endpoint["secret_ref"], "webhook", deliver
            )
            if 200 <= status < 300:
                state = "SUCCEEDED"
            else:
                error = {"message": "接收服务未确认事件"}
                if 300 <= status < 500 and status not in {408, 429}:
                    state = "FAILED"
        except ServiceError as exc:
            error = {"code": exc.code, "message": exc.message}
            if exc.status in {401, 403, 404, 409, 410}:
                state = "CANCELLED"
        if state == "RETRY" and current["cycle_attempts"] >= MAX_DELIVERY_ATTEMPTS:
            state = "FAILED"
        async with transaction(self.engine, context.scope, lock) as uow:
            latest = await delivery_repo.get(uow.connection, row["id"])
            if latest and latest["lease_nonce"] == nonce and latest["state"] == "SENDING":
                await delivery_repo.change(
                    uow,
                    row["id"],
                    latest["revision"],
                    {
                        "state": state,
                        "http_status": status,
                        "error": error,
                        "next_at": utcnow()
                        + timedelta(seconds=min(3600, 5 * 2 ** current["cycle_attempts"])),
                        "lease_nonce": None,
                        "lease_until": None,
                    },
                )

    async def sweep(self, channel_id: str) -> None:
        endpoints = await self.automation.channel_rows(channel_id, "webhook_endpoints")
        for endpoint in endpoints:
            if endpoint["state"] == "ACTIVE" and "run.terminal" in endpoint["events"]:
                try:
                    await self.terminal_events(endpoint)
                except ServiceError:
                    continue
        by_id = {r["id"]: r for r in endpoints}
        for row in await self.automation.channel_rows(channel_id, "webhook_deliveries"):
            if row["state"] not in {"PENDING", "RETRY", "SENDING"}:
                continue
            if target := by_id.get(row["endpoint_id"]):
                try:
                    await self.send(row, target)
                except ServiceError:
                    continue
