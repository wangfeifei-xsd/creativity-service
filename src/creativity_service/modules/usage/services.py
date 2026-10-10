"""Attempt 账本及补偿；迟报只修正费用，不修改任务终态。"""

from datetime import datetime, timedelta
from decimal import Decimal
from typing import Any, Literal

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncEngine

from creativity_service.core.context import Scope
from creativity_service.core.contracts import UsageEvent
from creativity_service.core.database import UnitOfWork, transaction
from creativity_service.core.database.soft_delete import active_rows
from creativity_service.core.primitives import ServiceError, canonical_json, digest, new_id, utcnow
from creativity_service.modules.budgets.services import BudgetService, record_cost
from creativity_service.modules.usage.pricing import calculate, normalize
from creativity_service.modules.usage.redaction import meter_only
from creativity_service.modules.usage.repositories import ledger_key, one, required, rows, save
from creativity_service.modules.usage.tables import metadata


class UsageService:
    def __init__(self, engine: AsyncEngine, budgets: BudgetService) -> None:
        self.engine, self.budgets = engine, budgets

    async def mark_sent(self, scope: Scope, attempt_id: str) -> None:
        async with transaction(self.engine, scope, [ledger_key(scope.channel_id)]) as uow:
            row = await required(
                uow.connection, "usage_records", scope.channel_id, attempt_id=attempt_id
            )
            self.budgets.verify_scope(row, scope)
            if row["state"] != "HELD":
                raise ServiceError("ATTEMPT_ALREADY_SENT", "尝试已发送、结算或释放", 409)
            # 先持久化发送意图再发网络请求；进程退出最多多保留预占，不能丢失费用。
            await save(uow, "usage_records", row["id"], {"state": "PENDING", "sent_at": utcnow()})
            for reservation in await rows(
                uow.connection,
                "budget_reservations",
                scope.channel_id,
                attempt_id=attempt_id,
                status="HELD",
            ):
                await save(uow, "budget_reservations", reservation["id"], {"status": "PENDING"})

    async def record_usage(self, uow: UnitOfWork, event: UsageEvent) -> None:
        await self.settle(event, uow=uow)

    async def settle(
        self,
        event: UsageEvent,
        *,
        outcome: Literal["SUCCEEDED", "FAILED", "UNKNOWN"] | None = None,
        uow: UnitOfWork | None = None,
    ) -> dict[str, Any]:
        if uow is None:
            async with transaction(
                self.engine, event.scope, [ledger_key(event.scope.channel_id)]
            ) as work:
                return await self.settle(event, outcome=outcome, uow=work)
        uow.require_scope(event.scope)
        await uow.acquire([ledger_key(event.scope.channel_id)])
        uow.require_lock(ledger_key(event.scope.channel_id))
        normalize(event.normalized_tokens, event.subset_relations)
        payload = event.model_dump(mode="json")
        if (
            len(canonical_json(payload)) > 65536
            or len(event.source_request_id) > 256
            or not event.source_request_id
        ):
            raise ServiceError("USAGE_INVALID", "用量事件过大或供应商请求标识无效", 422)
        row = await required(
            uow.connection, "usage_records", event.scope.channel_id, attempt_id=event.attempt_id
        )
        self.budgets.verify_scope(row, event.scope)
        # 未取得供应商标识时适配器使用本次 attempt_id；它不是永久的供应商绑定。
        bound_id = row["source_request_id"]
        local_id = event.attempt_id
        if row["connection_id"] != event.connection_id or (
            bound_id not in {None, local_id, event.source_request_id}
            and event.source_request_id != local_id
        ):
            raise ServiceError("SCOPE_MISMATCH", "供应商连接或请求与原始尝试不一致", 403)
        if event.source_request_id != local_id:
            owners = await rows(
                uow.connection,
                "usage_records",
                event.scope.channel_id,
                connection_id=event.connection_id,
                source_request_id=event.source_request_id,
            )
            if any(owner["attempt_id"] != event.attempt_id for owner in owners):
                raise ServiceError("USAGE_EVENT_CONFLICT", "供应商请求已绑定其他尝试", 409)
            if bound_id in {None, local_id}:
                row = await save(
                    uow, "usage_records", row["id"], {"source_request_id": event.source_request_id}
                )
        if outcome is not None:
            row = await self.finish_attempt(event.scope, event.attempt_id, outcome, uow=uow)
        existing = await one(
            uow.connection,
            "usage_events",
            event.scope.channel_id,
            connection_id=event.connection_id,
            source_request_id=event.source_request_id,
            event_version=event.event_version,
        )
        if existing:
            if (
                existing["payload_digest"] != digest(payload)
                or existing["attempt_id"] != event.attempt_id
            ):
                raise ServiceError("USAGE_EVENT_CONFLICT", "同一供应商事件版本内容冲突", 409)
            return row
        if bound_id not in {None, local_id} and event.source_request_id == local_id:
            # 真实标识绑定后仅允许重放已保存的临时事件，不能再提交新的临时用量。
            raise ServiceError("USAGE_EVENT_CONFLICT", "请使用已绑定的供应商请求标识", 409)
        previous = (
            await one(
                uow.connection, "usage_events", event.scope.channel_id, id=row["latest_event_id"]
            )
            if row["latest_event_id"]
            else None
        )

        def rank(value: dict[str, Any]) -> tuple[int, bool, int]:
            return (
                {"MISSING": 0, "ESTIMATED": 1, "REPORTED": 2}[value["status"]],
                value["final"],
                value["event_version"],
            )

        prior_payload = previous["event_payload"] if previous else None
        if (
            prior_payload
            and event.status == prior_payload["status"]
            and event.cumulative != prior_payload["cumulative"]
        ):
            raise ServiceError("USAGE_EVENT_CONFLICT", "同一请求不能混用累计和增量口径", 409)
        apply = prior_payload is None or rank(payload) > rank(prior_payload)
        if prior_payload and not event.cumulative and event.status == prior_payload["status"]:
            apply = apply or event.event_version <= prior_payload["event_version"]
        effective = (
            prior_payload if prior_payload and rank(prior_payload) > rank(payload) else payload
        )

        event_id = new_id("usage_event")
        await save(
            uow,
            "usage_events",
            event_id,
            {
                "attempt_id": event.attempt_id,
                "connection_id": event.connection_id,
                "source_request_id": event.source_request_id,
                "event_version": event.event_version,
                "raw_usage": meter_only(event.raw_usage),
                "usage_status": event.status,
                "observed_at": event.observed_at,
                "event_payload": {**payload, "raw_usage": meter_only(event.raw_usage)},
                "payload_digest": digest(payload),
                "applied": apply,
            },
        )
        if not apply:
            return row
        tokens = dict(event.normalized_tokens)
        if not event.cumulative:
            history = await rows(
                uow.connection, "usage_events", event.scope.channel_id, attempt_id=event.attempt_id
            )
            selected = [e["event_payload"] for e in history if e["usage_status"] == event.status]
            if any(
                e["cumulative"] or e["subset_relations"] != event.subset_relations for e in selected
            ):
                raise ServiceError("USAGE_EVENT_CONFLICT", "同一请求不能混用累计和增量口径", 409)
            dimensions = {k for e in selected for k in e["normalized_tokens"]}
            tokens = {
                k: None
                if any(e["normalized_tokens"].get(k) is None for e in selected)
                else sum(e["normalized_tokens"][k] for e in selected)
                for k in dimensions
            }
        normalize(tokens, event.subset_relations)
        price = (
            await one(
                uow.connection, "price_versions", event.scope.channel_id, id=row["price_version_id"]
            )
            if row["price_version_id"]
            else None
        )
        amount, pricing_status, calculation = calculate(
            price, tokens, event.subset_relations, event.status
        )
        updated = await save(
            uow,
            "usage_records",
            row["id"],
            {
                "source_request_id": event.source_request_id,
                "normalized_tokens": tokens,
                "subset_relations": event.subset_relations,
                "input_tokens": tokens.get("input"),
                "output_tokens": tokens.get("output"),
                "cached_tokens": tokens.get("cached", tokens.get("cache_read")),
                "reasoning_tokens": tokens.get("reasoning"),
                "raw_usage_ref": event_id,
                "usage_status": event.status,
                "pricing_status": pricing_status,
                "amount": amount,
                "calculation": calculation,
                "latest_event_version": effective["event_version"],
                "latest_event_id": event_id if effective is payload else row["latest_event_id"],
                "final_reported": event.status == "REPORTED" and effective["final"],
                "sent_at": row["sent_at"] or event.observed_at,
                "state": "PENDING",
                "outcome": row["outcome"],
            },
        )
        await self.adjustment(
            uow,
            row,
            updated,
            event_id,
            "供应商用量修正" if row["latest_event_id"] else "首次用量核算",
        )
        updated = await self.reconcile_reservations(uow, updated)
        await self.budgets.refresh_alerts(uow, utcnow())
        return updated

    async def adjustment(
        self,
        uow: UnitOfWork,
        old: dict[str, Any],
        new: dict[str, Any],
        event_id: str | None,
        reason: str,
    ) -> None:
        same_currency = old["currency"] == new["currency"]
        delta = (
            new["amount"] - old["amount"]
            if same_currency and old["amount"] is not None and new["amount"] is not None
            else None
        )
        fields = (
            "usage_status",
            "pricing_status",
            "amount",
            "currency",
            "price_version_id",
            "normalized_tokens",
            "calculation",
        )

        def snapshot(row: dict[str, Any]) -> dict[str, Any]:
            return {k: str(row[k]) if isinstance(row[k], Decimal) else row[k] for k in fields}

        await save(
            uow,
            "usage_adjustments",
            new_id("adjustment"),
            {
                "usage_id": new["id"],
                "event_id": event_id,
                "previous_revision": old["revision"],
                "amount_delta": delta,
                "currency": new["currency"],
                "reason": reason,
                "calculation": {"before": snapshot(old), "after": snapshot(new)},
            },
        )

    async def reconcile_reservations(self, uow: UnitOfWork, row: dict[str, Any]) -> dict[str, Any]:
        complete = row["final_reported"]
        reservations = await rows(
            uow.connection,
            "budget_reservations",
            uow.scope.channel_id,
            attempt_id=row["attempt_id"],
        )
        for reservation in reservations:
            if reservation["status"] == "RELEASED":
                continue
            actual = record_cost(row, reservation["unit"], exposure=False)
            if reservation["unit"] == "amount" and row["currency"] != reservation["currency"]:
                actual = None
            final = bool(row["final_reported"] and actual is not None)
            complete = complete and final
            await save(
                uow,
                "budget_reservations",
                reservation["id"],
                {
                    "settled_amount": actual,
                    "status": "SETTLED" if final else "PENDING",
                },
            )
        return await save(
            uow, "usage_records", row["id"], {"state": "SETTLED" if complete else "PENDING"}
        )

    async def finish_attempt(
        self,
        scope: Scope,
        attempt_id: str,
        outcome: Literal["SUCCEEDED", "FAILED", "UNKNOWN"],
        *,
        confirmed_unsent: bool = False,
        uow: UnitOfWork | None = None,
    ) -> dict[str, Any]:
        if uow is None:
            async with transaction(self.engine, scope, [ledger_key(scope.channel_id)]) as work:
                return await self.finish_attempt(
                    scope, attempt_id, outcome, confirmed_unsent=confirmed_unsent, uow=work
                )
        uow.require_scope(scope)
        await uow.acquire([ledger_key(scope.channel_id)])
        uow.require_lock(ledger_key(scope.channel_id))
        row = await required(
            uow.connection, "usage_records", scope.channel_id, attempt_id=attempt_id
        )
        self.budgets.verify_scope(row, scope)
        # 进程恢复只能补记未知，不能覆盖适配器已经确认的调用结果。
        if row["outcome"] != outcome and row["outcome"] in {"PENDING", "UNKNOWN"}:
            row = await save(uow, "usage_records", row["id"], {"outcome": outcome})
        if confirmed_unsent:
            await self.release_unused(scope, attempt_id, confirmed_unsent=True, uow=uow)
            row = await required(uow.connection, "usage_records", scope.channel_id, id=row["id"])
        return row

    async def release_unused(
        self,
        scope: Scope,
        attempt_id: str,
        *,
        expired_before: datetime | None = None,
        confirmed_unsent: bool = False,
        uow: UnitOfWork | None = None,
    ) -> str:
        if uow is None:
            async with transaction(self.engine, scope, [ledger_key(scope.channel_id)]) as work:
                return await self.release_unused(
                    scope,
                    attempt_id,
                    expired_before=expired_before,
                    confirmed_unsent=confirmed_unsent,
                    uow=work,
                )
        uow.require_scope(scope)
        await uow.acquire([ledger_key(scope.channel_id)])
        uow.require_lock(ledger_key(scope.channel_id))
        row = await required(
            uow.connection, "usage_records", scope.channel_id, attempt_id=attempt_id
        )
        self.budgets.verify_scope(row, scope)
        if row["state"] in {"SETTLED", "RELEASED"}:
            return str(row["state"])
        if expired_before is not None and row["state"] == "HELD":
            current_reservations = await rows(
                uow.connection, "budget_reservations", scope.channel_id, attempt_id=attempt_id
            )
            expires_at = max(
                (r["expires_at"] for r in current_reservations if r["status"] == "HELD"),
                default=row["updated_at"] + timedelta(minutes=15),
            )
            if expires_at > expired_before:
                return "HELD"
        proven_unsent = (
            confirmed_unsent
            and row["usage_status"] == "MISSING"
            and row["source_request_id"] in {None, attempt_id}
        )
        if row["sent_at"] is not None and not proven_unsent:
            # 客户端取消、租约失效及供应商超时都不证明没有计费。
            return "PENDING"
        await save(uow, "usage_records", row["id"], {"state": "RELEASED"})
        for reservation in await rows(
            uow.connection, "budget_reservations", scope.channel_id, attempt_id=attempt_id
        ):
            if reservation["status"] != "RELEASED":
                await save(
                    uow,
                    "budget_reservations",
                    reservation["id"],
                    {"status": "RELEASED", "settled_amount": Decimal(0)},
                )
        await self.budgets.refresh_alerts(uow, utcnow())
        return "RELEASED"

    async def compensate(self, scope: Scope) -> dict[str, int]:
        await self.reconcile_outcomes(scope)
        now = utcnow()
        async with self.engine.connect() as connection:
            records = await rows(connection, "usage_records", scope.channel_id)
        counts = {"released": 0, "pending": 0}
        for row in records:
            if row["state"] not in {"HELD", "PENDING"}:
                continue
            async with self.engine.connect() as connection:
                reservations = await rows(
                    connection,
                    "budget_reservations",
                    scope.channel_id,
                    attempt_id=row["attempt_id"],
                )
            expires_at = max(
                (r["expires_at"] for r in reservations if r["status"] in {"HELD", "PENDING"}),
                default=row["updated_at"] + timedelta(minutes=15),
            )
            if expires_at > now:
                continue
            original = Scope.model_validate({k: row[k] for k in Scope.model_fields})
            state = await self.release_unused(original, row["attempt_id"], expired_before=now)
            if state in {"RELEASED", "PENDING"}:
                counts["released" if state == "RELEASED" else "pending"] += 1

        return counts

    async def reconcile_outcomes(self, scope: Scope, limit: int = 100) -> int:
        """分批修复历史交接遗漏，只采用同渠道、同运行、同尝试的已存终态。"""
        from creativity_service.modules.runs.tables import metadata as runs

        if not 1 <= limit <= 500:
            raise ValueError("调用结果修复批量必须在 1 到 500 之间")
        usage, attempts = metadata.tables["usage_records"], runs.tables["attempts"]
        async with transaction(self.engine, scope, [ledger_key(scope.channel_id)]) as uow:
            records = list(
                (
                    await uow.connection.execute(
                        active_rows(
                            select(usage.c.id, attempts.c.state)
                            .select_from(
                                usage.join(
                                    attempts,
                                    (attempts.c.channel_id == usage.c.channel_id)
                                    & (attempts.c.run_id == usage.c.run_id)
                                    & (attempts.c.id == usage.c.attempt_id)
                                    & (attempts.c.usage_id == usage.c.id),
                                )
                            )
                            .where(
                                usage.c.channel_id == scope.channel_id,
                                usage.c.outcome == "PENDING",
                                attempts.c.state.in_(["SUCCEEDED", "FAILED", "UNKNOWN"]),
                            )
                            .order_by(usage.c.created_at, usage.c.id)
                            .limit(limit)
                        )
                    )
                ).mappings()
            )
            for state in ("SUCCEEDED", "FAILED", "UNKNOWN"):
                identifiers = [r["id"] for r in records if r["state"] == state]
                if identifiers:
                    await uow.connection.execute(
                        update(usage)
                        .where(
                            usage.c.channel_id == scope.channel_id,
                            usage.c.id.in_(identifiers),
                            usage.c.outcome == "PENDING",
                        )
                        .values(outcome=state, revision=usage.c.revision + 1, updated_at=utcnow())
                    )
            return len(records)

    async def reprice(
        self, scope: Scope, usage_id: str, price_id: str, revision: int
    ) -> dict[str, Any]:
        async with transaction(self.engine, scope, [ledger_key(scope.channel_id)]) as uow:
            row = await required(uow.connection, "usage_records", scope.channel_id, id=usage_id)
            if row["revision"] != revision:
                raise ServiceError("REVISION_CONFLICT", "账本已更新，请刷新后重试", 409)
            price = await required(uow.connection, "price_versions", scope.channel_id, id=price_id)
            if row["model_id"] != price["model_id"]:
                raise ServiceError("PRICE_INVALID", "价格与原始模型不一致", 422)
            amount, status, calculation = calculate(
                price, row["normalized_tokens"], row["subset_relations"], row["usage_status"]
            )
            changed = await save(
                uow,
                "usage_records",
                row["id"],
                {
                    "price_version_id": price_id,
                    "currency": price["currency"],
                    "amount": amount,
                    "pricing_status": status,
                    "calculation": calculation,
                },
            )
            await self.adjustment(uow, row, changed, None, "价格版本重算")
            changed = await self.reconcile_reservations(uow, changed)
            await self.budgets.refresh_alerts(uow, utcnow())
            return changed
