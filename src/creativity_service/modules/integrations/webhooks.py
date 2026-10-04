"""最小事件、固定目的地址与签名密钥引用；投递在短事务之外执行。"""

import hashlib
import hmac
import secrets
from datetime import timedelta
from typing import Any

from pydantic import SecretBytes
from sqlalchemy import or_, select, tuple_

from creativity_service.core.context import AuthContext, TaskEnvelope
from creativity_service.core.database import Repository, transaction
from creativity_service.core.deletion import ContentRef, DeletionGuard
from creativity_service.core.primitives import ServiceError, canonical_json, digest, new_id, utcnow
from creativity_service.core.security.credentials import CredentialService, KeyProvider
from creativity_service.core.security.outbound import OutboundPolicy
from creativity_service.integrations.outbound import BoundedHttp
from creativity_service.modules.channels.repositories import required as channel_required
from creativity_service.modules.iam.reading import resource_state
from creativity_service.modules.integrations.authorization import (
    management_policy,
    require_management,
)
from creativity_service.modules.integrations.automation import (
    AutomationService,
    add,
    audit_configuration,
    configuration_keys,
    keys,
    owner,
    repo,
    scope_of,
    worker_identity,
)
from creativity_service.modules.integrations.automation_schemas import WebhookCreate, WebhookUpdate
from creativity_service.modules.integrations.automation_tables import metadata
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
            table = metadata.tables["webhook_deliveries"]
            records = [
                dict(row)
                for row in (
                    await connection.execute(
                        select(table)
                        .where(
                            repo(context.scope, "webhook_deliveries").predicate(),
                            table.c.owner_key == owner(context),
                            table.c.state != "DELETED",
                        )
                        .order_by(table.c.created_at.desc(), table.c.id)
                        .limit(500)
                    )
                ).mappings()
            ]
            endpoints = await repo(context.scope, "webhook_endpoints").get_many(
                connection, [row["endpoint_id"] for row in records]
            )
        names = {identifier: row["name"] for identifier, row in endpoints.items()}
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
            for r in records
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
        if endpoint["state"] != "ACTIVE" or "run.terminal" not in endpoint["events"]:
            return
        context = AuthContext.model_validate(endpoint["identity"])
        await self.automation.manage(context)
        table, deliveries = runs_metadata.tables["runs"], metadata.tables["webhook_deliveries"]
        existing = (
            select(deliveries.c.id)
            .where(
                repo(context.scope, "webhook_deliveries").predicate(),
                deliveries.c.endpoint_id == endpoint["id"],
                deliveries.c.kind == "run.terminal",
                deliveries.c.payload["run_id"].astext == table.c.id,
            )
            .exists()
        )
        after = None
        while True:
            statement = select(table).where(
                self.subscriptions.predicate(context, endpoint["client_ids"]),
                table.c.state.in_(TERMINAL),
                table.c.updated_at >= endpoint["created_at"],
                ~existing,
            )
            if after is not None:
                statement = statement.where(tuple_(table.c.updated_at, table.c.id) > after)
            async with self.engine.connect() as connection:
                batch = [
                    dict(row)
                    for row in (
                        await connection.execute(
                            statement.order_by(table.c.updated_at, table.c.id).limit(100)
                        )
                    ).mappings()
                ]
            if not batch:
                return
            await self.enqueue_terminals(context, endpoint, batch)
            if len(batch) < 100:
                return
            after = (batch[-1]["updated_at"], batch[-1]["id"])

    async def enqueue_terminals(
        self, context: AuthContext, endpoint: dict[str, Any], candidates: list[dict[str, Any]]
    ) -> None:
        events = {r["id"]: digest(["run.terminal", r["id"], r["state"]]) for r in candidates}
        identifiers = {run_id: digest([endpoint["id"], event]) for run_id, event in events.items()}
        sources = {
            run_id: (ContentRef("webhook_endpoint", endpoint["id"]), ContentRef("run", run_id))
            for run_id in events
        }
        links = [
            (
                digest([identifiers[run_id], ref.resource_type, ref.resource_id]),
                ref,
                ContentRef("webhook_delivery", identifiers[run_id]),
                None,
            )
            for run_id, refs in sources.items()
            for ref in refs
        ]
        async with transaction(
            self.engine,
            context.scope,
            keys(
                context,
                ("webhook_endpoints", endpoint["id"]),
                *(("webhook_deliveries", identifier) for identifier in identifiers.values()),
                *(("source_links", link[0]) for link in links),
            ),
        ) as uow:
            actual = await repo(context.scope, "webhook_endpoints").get(
                uow.connection, endpoint["id"]
            )
            if (
                not actual
                or actual["state"] != "ACTIVE"
                or actual["revision"] != endpoint["revision"]
            ):
                return
            # 新写入边界内重读授权与来源；同一事务中的每条事件共用此批快照。
            policy = await management_policy(uow, context)
            if "integration:manage" not in policy.actions("channel", context.scope.channel_id):
                raise ServiceError("FORBIDDEN", "无权管理此业务范围", 403)
            domain = await channel_required(
                uow.connection,
                "data_scopes",
                context.scope.channel_id,
                id=context.scope.data_scope_id,
                environment=context.scope.environment,
            )
            if domain["status"] != "ACTIVE":
                raise ServiceError("DATA_SCOPE_DISABLED", "业务数据域不可用", 403)
            clients = await self.subscriptions.clients(
                uow.connection, context, actual["client_ids"]
            )
            table = runs_metadata.tables["runs"]
            current = [
                dict(row)
                for row in (
                    await uow.connection.execute(
                        select(table).where(
                            self.subscriptions.predicate(context, actual["client_ids"]),
                            table.c.id.in_(events),
                            table.c.state.in_(TERMINAL),
                        )
                    )
                ).mappings()
            ]
            guard = DeletionGuard(context.scope)
            await guard.check(uow, [ContentRef("webhook_endpoint", endpoint["id"])])
            blocked = await guard.blocked_refs(
                uow,
                [ContentRef("run", r["id"]) for r in current],
                scopes=[scope_of(r) for r in current],
            )
            repository = repo(context.scope, "webhook_deliveries")
            stored = await repository.get_many(uow.connection, identifiers.values())
            additions = {}
            for row in current:
                identifier = identifiers[row["id"]]
                if identifier in stored or ContentRef("run", row["id"]) in blocked:
                    continue
                try:
                    self.subscriptions.match(context, actual["client_ids"], row, clients)
                    scoped = context.model_copy(update={"scope": scope_of(row)})
                    if "run:read" not in policy.actions(
                        "run", row["id"], resource_state(scoped, "run", row), context=scoped
                    ):
                        continue
                except ServiceError:
                    continue
                additions[identifier] = {
                    "endpoint_id": endpoint["id"],
                    "event_id": events[row["id"]],
                    "kind": "run.terminal",
                    "payload": {
                        "event_id": events[row["id"]],
                        "type": "run.terminal",
                        "run_id": row["id"],
                        "state": row["state"],
                        "occurred_at": row["updated_at"].isoformat(),
                        "status_path": ("/api/v1/runs/" if row["client_id"] else "/admin/v1/runs/")
                        + row["id"],
                    },
                    "owner_key": endpoint["owner_key"],
                    "state": "PENDING",
                    "attempts": 0,
                    "cycle_attempts": 0,
                    "next_at": utcnow(),
                    "error": None,
                    "http_status": None,
                    "lease_nonce": None,
                    "lease_until": None,
                }
            await repository.add_many(uow, additions)
            await guard.link_many(uow, [link for link in links if link[2].resource_id in additions])

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
        endpoints, deliveries = (
            metadata.tables["webhook_endpoints"],
            metadata.tables["webhook_deliveries"],
        )
        after = ""
        while True:
            async with self.engine.connect() as connection:
                batch = [
                    dict(row)
                    for row in (
                        await connection.execute(
                            select(endpoints)
                            .where(
                                endpoints.c.channel_id == channel_id,
                                endpoints.c.state == "ACTIVE",
                                endpoints.c.events.contains(["run.terminal"]),
                                endpoints.c.id > after,
                            )
                            .order_by(endpoints.c.id)
                            .limit(100)
                        )
                    ).mappings()
                ]
            for endpoint in batch:
                try:
                    await self.terminal_events(endpoint)
                except ServiceError:
                    continue
            if len(batch) < 100:
                break
            after = batch[-1]["id"]
        after = ""
        now = utcnow()
        while True:
            async with self.engine.connect() as connection:
                batch = [
                    dict(row)
                    for row in (
                        await connection.execute(
                            select(deliveries)
                            .where(
                                deliveries.c.channel_id == channel_id,
                                deliveries.c.state.in_(["PENDING", "RETRY", "SENDING"]),
                                deliveries.c.next_at <= now,
                                or_(
                                    deliveries.c.lease_until.is_(None),
                                    deliveries.c.lease_until <= now,
                                ),
                                deliveries.c.id > after,
                            )
                            .order_by(deliveries.c.id)
                            .limit(100)
                        )
                    ).mappings()
                ]
                targets = (
                    {
                        r["id"]: dict(r)
                        for r in (
                            await connection.execute(
                                select(endpoints).where(
                                    endpoints.c.channel_id == channel_id,
                                    endpoints.c.id.in_([row["endpoint_id"] for row in batch]),
                                )
                            )
                        ).mappings()
                    }
                    if batch
                    else {}
                )
            for row in batch:
                if target := targets.get(row["endpoint_id"]):
                    try:
                        await self.send(row, target)
                    except ServiceError:
                        continue
            if len(batch) < 100:
                break
            after = batch[-1]["id"]
