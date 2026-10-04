"""短事务内准入及预算预占；供应商网络调用必须在事务提交之后。"""

from collections.abc import Awaitable, Callable, Mapping
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from functools import wraps
from typing import Any

from sqlalchemy import or_, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncEngine

from creativity_service.core.context import AuthContext, Scope
from creativity_service.core.contracts import Admission, BudgetReservation, UsageEvent
from creativity_service.core.database import UnitOfWork, transaction
from creativity_service.core.locking import ResourceKey
from creativity_service.core.primitives import Money, ServiceError, digest, new_id, utcnow
from creativity_service.modules.usage.pricing import calculate, normalize, timezone
from creativity_service.modules.usage.repositories import ledger_key, one, platform_key, rows, save
from creativity_service.modules.usage.schemas import AttemptPlan, ReservationReceipt
from creativity_service.modules.usage.tables import metadata


def control_errors[**P, R](method: Callable[P, Awaitable[R]]) -> Callable[P, Awaitable[R]]:
    @wraps(method)
    async def call(*args: P.args, **kwargs: P.kwargs) -> R:
        try:
            return await method(*args, **kwargs)
        except SQLAlchemyError as exc:
            raise ServiceError(
                "BUDGET_CONTROL_UNAVAILABLE", "预算控制存储暂不可用，请稍后重试", 503
            ) from exc

    return call


def period_start(now: datetime, period: str, zone: str) -> datetime:
    local = now.astimezone(timezone(zone))
    if period == "month":
        local = local.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    elif period == "day":
        local = local.replace(hour=0, minute=0, second=0, microsecond=0)
    elif period == "hour":
        local = local.replace(minute=0, second=0, microsecond=0)
    elif period == "minute":
        local = local.replace(second=0, microsecond=0)
    else:
        raise ServiceError("VALIDATION_ERROR", "预算周期无效", 422)
    return local.astimezone(UTC)


def matches(policy: dict[str, Any], snapshot: dict[str, Any]) -> bool:
    kind = policy["scope_type"]
    return bool(kind == "channel" or policy["scope_id"] == snapshot.get(f"{kind}_id"))


def token_total(tokens: Mapping[str, int | None], relations: dict[str, str]) -> Decimal | None:
    if tokens.get("input") is None or tokens.get("output") is None:
        return None
    roots = [v for k, v in tokens.items() if k not in relations]
    if any(v is None for v in roots):
        return None
    return Decimal(sum(v for v in roots if v is not None))


def record_cost(row: dict[str, Any], unit: str, *, exposure: bool) -> Decimal | None:
    if row["state"] == "RELEASED":
        return Decimal(0)
    if unit in {"attempts", "requests"}:
        return Decimal(1)
    if unit == "concurrency":
        return (
            Decimal(int(row["state"] in {"HELD", "PENDING"}))
            if exposure
            else Decimal(0)
            if row["final_reported"]
            else None
        )
    if unit == "tokens":
        actual = token_total(row["normalized_tokens"], row["subset_relations"])
        upper = token_total(row["upper_tokens"], row["subset_relations"])
    else:
        actual, upper = row["amount"], row["upper_amount"]
    if exposure and row["state"] in {"HELD", "PENDING"}:
        known = [v for v in (actual, upper) if v is not None]
        return max(known) if known else None
    return actual


async def active_price(uow: UnitOfWork, model_id: str, at: datetime) -> dict[str, Any] | None:
    prices = await rows(uow.connection, "price_versions", uow.scope.channel_id, model_id=model_id)
    choices = [p for p in prices if p["effective_at"] <= at]
    return (
        max(choices, key=lambda p: (p["effective_at"], p["created_at"], p["id"]))
        if choices
        else None
    )


class BudgetService:
    def __init__(self, engine: AsyncEngine) -> None:
        self.engine = engine
        self.available = True

    def admission_keys(self, context: AuthContext, run_id: str) -> list[ResourceKey]:
        # 策略登记与所有命中额度共用渠道粗粒度锁，避免配置变化造成漏锁。
        # 系统锁只串行平台计数，不承载业务账本；公共工作单元统一排序一次获取。
        return [ledger_key(context.scope.channel_id), platform_key()]

    def reservation_keys(self, context: AuthContext, attempt_id: str) -> list[ResourceKey]:
        return [ledger_key(context.scope.channel_id)]

    def require(self, uow: UnitOfWork, context: AuthContext) -> None:
        uow.require_scope(context.scope)
        uow.require_lock(ledger_key(context.scope.channel_id))
        if not self.available:
            raise ServiceError("BUDGET_CONTROL_UNAVAILABLE", "预算控制暂不可用，请稍后重试", 503)

    async def policies(self, uow: UnitOfWork) -> list[dict[str, Any]]:
        return await rows(uow.connection, "budget_policies", uow.scope.channel_id, status="ACTIVE")

    async def exposure_data(
        self, uow: UnitOfWork, policies: list[dict[str, Any]], now: datetime
    ) -> dict[str, list[dict[str, Any]]]:
        uow.require_lock(ledger_key(uow.scope.channel_id))
        result: dict[str, list[dict[str, Any]]] = {"admissions": [], "usage_records": []}
        for name in result:
            selected = [
                p
                for p in policies
                if (p["unit"] in {"requests", "concurrency"} and p["scope_type"] != "model")
                == (name == "admissions")
            ]
            if not selected:
                continue
            table = metadata.tables[name]
            periods = [
                period_start(now, p["period"], p["timezone"])
                for p in selected
                if p["unit"] != "concurrency"
            ]
            predicates = []
            if periods:
                predicates.append(table.c.created_at >= min(periods))
            if any(p["unit"] == "concurrency" for p in selected):
                predicates.append(
                    table.c.status == "HELD"
                    if name == "admissions"
                    else table.c.state.in_(["HELD", "PENDING"])
                )
            result[name] = [
                dict(r)
                for r in (
                    await uow.connection.execute(
                        select(table).where(
                            table.c.channel_id == uow.scope.channel_id, or_(*predicates)
                        )
                    )
                ).mappings()
            ]
        return result

    async def exposure(
        self,
        uow: UnitOfWork,
        policy: dict[str, Any],
        now: datetime,
        data: dict[str, list[dict[str, Any]]] | None = None,
    ) -> Decimal:
        data = data if data is not None else await self.exposure_data(uow, [policy], now)
        start = period_start(now, policy["period"], policy["timezone"])
        if policy["unit"] in {"requests", "concurrency"} and policy["scope_type"] != "model":
            admissions = data["admissions"]
            return Decimal(
                sum(
                    1
                    for a in admissions
                    if matches(policy, a["snapshot"])
                    and (
                        a["status"] == "HELD"
                        if policy["unit"] == "concurrency"
                        else a["created_at"] >= start
                    )
                )
            )
        records = data["usage_records"]
        total = Decimal(0)
        for row in records:
            if (
                (row["created_at"] < start and policy["unit"] != "concurrency")
                or not matches(policy, row["snapshot"])
                or row["state"] == "RELEASED"
            ):
                continue
            if policy["unit"] == "amount" and row["currency"] != policy["currency"]:
                # 不同币种绝不直接相加；金额硬预算下未知币种或异币种必须拒绝继续消费。
                if policy["mode"] == "HARD":
                    raise ServiceError("BUDGET_PRICE_REQUIRED", "预算币种与已有调用价格不一致", 429)
                continue
            value = record_cost(row, policy["unit"], exposure=True)
            if value is None:
                if policy["mode"] == "HARD":
                    raise ServiceError(
                        "BUDGET_PRICE_REQUIRED", "已有调用成本待核实，暂不能增加受控调用", 429
                    )
                continue
            total += value
        return total

    @staticmethod
    def snapshot(context: AuthContext, plan: AttemptPlan | None) -> dict[str, Any]:
        return {
            **context.scope.model_dump(mode="json"),
            "client_id": context.client_id,
            "key_id": context.key_id,
            "actor_id": context.actor_id,
            "model_id": plan.model_id if plan else None,
            "agent_id": plan.agent_id if plan else None,
            "purpose": plan.purpose if plan else None,
            "names": plan.names if plan else {},
        }

    async def estimate(
        self, uow: UnitOfWork, plan: AttemptPlan
    ) -> tuple[dict[str, int], dict[str, Any] | None, Decimal | None]:
        if set(plan.additional_upper_tokens) & {"input", "output"}:
            raise ServiceError("USAGE_INVALID", "扩展维度不能覆盖输入输出上限", 422)
        tokens = {
            "input": plan.input_tokens,
            "output": plan.max_output_tokens,
            **plan.additional_upper_tokens,
        }
        for child in plan.subset_relations:
            tokens.setdefault(child, 0)
        normalize(tokens, plan.subset_relations)
        price = await active_price(uow, plan.model_id, utcnow())
        amount, _, _ = calculate(price, tokens, plan.subset_relations, "ESTIMATED", upper=True)
        return tokens, price, amount

    @control_errors
    async def admit(
        self, uow: UnitOfWork, context: AuthContext, run_id: str, plan: AttemptPlan | None = None
    ) -> Admission:
        self.require(uow, context)
        uow.require_lock(platform_key())
        if plan and plan.run_id != run_id:
            raise ServiceError("SCOPE_MISMATCH", "候选调用与受理运行不一致", 422)
        existing = await one(uow.connection, "admissions", context.scope.channel_id, run_id=run_id)
        if existing:
            self.verify_scope(existing, context.scope)
            self.verify_source(existing, context, plan)
            return self.admission_view(existing, context.scope)
        snapshot, now = self.snapshot(context, plan), utcnow()
        tokens, price, upper = await self.estimate(uow, plan) if plan else ({}, None, None)
        selected = [p for p in await self.policies(uow) if matches(p, snapshot)]
        exposure = await self.exposure_data(uow, selected, now)
        for policy in selected:
            used = await self.exposure(uow, policy, now, exposure)
            if policy["mode"] != "HARD":
                continue
            if policy["unit"] == "amount":
                if price is None or upper is None or price["currency"] != policy["currency"]:
                    raise ServiceError(
                        "BUDGET_PRICE_REQUIRED", "金额硬预算要求有效价格与调用上限", 429
                    )
                increment = upper
            elif policy["unit"] == "tokens":
                token_increment = token_total(tokens, plan.subset_relations if plan else {})
                if token_increment is None:
                    raise ServiceError("BUDGET_ESTIMATE_REQUIRED", "Token 预算需要调用上限", 429)
            else:
                increment = Decimal(1)
            if policy["unit"] == "tokens":
                assert token_increment is not None
                increment = token_increment
            if used + increment > policy["limit_value"]:
                raise ServiceError("BUDGET_EXCEEDED", "请求量、并发或预算已达上限", 429)
        limits = await rows(uow.connection, "platform_limits", "system")
        current: dict[str, dict[str, Any]] = {}
        for limit in limits:
            if limit["effective_at"] <= now and (
                limit["limit_code"] not in current
                or limit["created_at"] > current[limit["limit_code"]]["created_at"]
            ):
                current[limit["limit_code"]] = limit
        occupancies = await rows(uow.connection, "platform_quota_occupancies", "system")
        for limit in current.values():
            if limit["status"] != "ACTIVE":
                continue
            start = period_start(now, limit["period"], limit["timezone"])
            platform_used = sum(
                1
                for o in occupancies
                if o["limit_code"] == limit["limit_code"]
                and (
                    o["status"] == "HELD"
                    if limit["unit"] == "concurrency"
                    else o["created_at"] >= start
                )
            )
            if platform_used + 1 > limit["limit_value"]:
                raise ServiceError("PLATFORM_LIMIT_EXCEEDED", "平台请求量或并发已达上限", 429)
            await save(
                uow,
                "platform_quota_occupancies",
                new_id("quota"),
                {
                    "target_channel_id": context.scope.channel_id,
                    "run_id": run_id,
                    "limit_id": limit["id"],
                    "limit_code": limit["limit_code"],
                    "period_start": start,
                    "unit": limit["unit"],
                    "status": "HELD",
                },
                system=True,
            )
        row = await save(
            uow,
            "admissions",
            new_id("admission"),
            {
                **context.scope.model_dump(exclude={"channel_id"}),
                "run_id": run_id,
                "policy_refs": [{"id": p["id"], "version_id": p["version_id"]} for p in selected],
                "snapshot": snapshot,
                "status": "HELD",
                "expires_at": now + timedelta(minutes=15),
            },
        )
        await self.refresh_alerts(uow, now)
        return self.admission_view(row, context.scope)

    @staticmethod
    def verify_source(
        admission: dict[str, Any], context: AuthContext, plan: AttemptPlan | None
    ) -> None:
        original = admission["snapshot"]
        if any(
            original.get(key) != getattr(context, key)
            for key in ("client_id", "key_id", "actor_id")
        ):
            raise ServiceError("SCOPE_MISMATCH", "运行来源身份创建后不能改绑", 403)
        if plan and any(
            original.get(key) is not None and original[key] != getattr(plan, key)
            for key in ("agent_id", "purpose")
        ):
            raise ServiceError("SCOPE_MISMATCH", "运行的智能体及用途创建后不能改绑", 403)

    @staticmethod
    def verify_scope(row: dict[str, Any], scope: Scope) -> None:
        if any(row[k] != v for k, v in scope.model_dump().items() if k in row):
            raise ServiceError("SCOPE_MISMATCH", "调用原始范围与服务上下文不一致", 403)

    @staticmethod
    def admission_view(row: dict[str, Any], scope: Scope) -> Admission:
        return Admission(
            admission_id=row["id"],
            scope=scope,
            run_id=row["run_id"],
            policy_ids=tuple(p["id"] for p in row["policy_refs"]),
            state=row["status"],
            expires_at=row["expires_at"],
        )

    @control_errors
    async def reserve_attempt(
        self, context: AuthContext, plan: AttemptPlan, *, uow: UnitOfWork | None = None
    ) -> ReservationReceipt:
        if uow is None:
            async with transaction(
                self.engine, context.scope, self.reservation_keys(context, plan.attempt_id)
            ) as work:
                return await self.reserve_attempt(context, plan, uow=work)
        self.require(uow, context)
        admission = await one(
            uow.connection, "admissions", context.scope.channel_id, run_id=plan.run_id
        )
        if admission is None or admission["status"] != "HELD":
            raise ServiceError("ADMISSION_REQUIRED", "调用前必须完成运行准入", 409)
        self.verify_scope(admission, context.scope)
        self.verify_source(admission, context, plan)
        if admission["snapshot"].get("agent_id") is None:
            await save(
                uow, "admissions", admission["id"], {"snapshot": self.snapshot(context, plan)}
            )
        existing = await one(
            uow.connection, "usage_records", context.scope.channel_id, attempt_id=plan.attempt_id
        )
        snapshot = self.snapshot(context, plan)
        if existing:
            self.verify_scope(existing, context.scope)
            if (
                existing["snapshot"] != snapshot
                or existing["run_id"] != plan.run_id
                or existing["connection_id"] != plan.connection_id
            ):
                raise ServiceError("ATTEMPT_CONFLICT", "同一尝试不能改绑运行、模型或来源", 409)
            if existing["state"] != "HELD":
                raise ServiceError("ATTEMPT_ALREADY_SENT", "已发送或已释放的尝试不能重新预占", 409)
        tokens, price, upper = await self.estimate(uow, plan)
        policies = [
            p
            for p in await self.policies(uow)
            if matches(p, snapshot)
            and (p["unit"] not in {"requests", "concurrency"} or p["scope_type"] == "model")
        ]
        now = utcnow()
        allocations: list[tuple[dict[str, Any], Decimal]] = []
        exposure = await self.exposure_data(uow, policies, now)
        for policy in policies:
            quantity = (
                Decimal(1)
                if policy["unit"] in {"attempts", "requests", "concurrency"}
                else (
                    token_total(tokens, plan.subset_relations)
                    if policy["unit"] == "tokens"
                    else upper
                )
            )
            if policy["unit"] == "amount" and (
                price is None or price["currency"] != policy["currency"]
            ):
                quantity = None
            if quantity is None:
                if policy["mode"] == "HARD":
                    raise ServiceError(
                        "BUDGET_PRICE_REQUIRED", "金额硬预算要求匹配币种的有效价格与上限", 429
                    )
                continue
            used = await self.exposure(uow, policy, now, exposure)
            if existing and existing["created_at"] >= period_start(
                now, policy["period"], policy["timezone"]
            ):
                used -= record_cost(existing, policy["unit"], exposure=True) or Decimal(0)
            if policy["mode"] == "HARD" and used + quantity > policy["limit_value"]:
                raise ServiceError("BUDGET_EXCEEDED", "本次调用将超过预算", 429)
            allocations.append((policy, quantity))
        record_id = existing["id"] if existing else new_id("usage")
        row = await save(
            uow,
            "usage_records",
            record_id,
            {
                **context.scope.model_dump(exclude={"channel_id"}),
                "run_id": plan.run_id,
                "attempt_id": plan.attempt_id,
                "source_type": plan.source_type,
                "client_id": context.client_id,
                "key_id": context.key_id,
                "actor_id": context.actor_id,
                "agent_id": plan.agent_id,
                "model_id": plan.model_id,
                "connection_id": plan.connection_id,
                "purpose": plan.purpose,
                "snapshot": snapshot,
                "input_tokens": None,
                "output_tokens": None,
                "cached_tokens": None,
                "reasoning_tokens": None,
                "raw_usage_ref": None,
                "usage_status": "MISSING",
                "pricing_status": "UNPRICED",
                "price_version_id": price["id"] if price else None,
                "amount": None,
                "currency": price["currency"] if price else None,
                "normalized_tokens": {},
                "subset_relations": plan.subset_relations,
                "calculation": {},
                "upper_tokens": tokens,
                "upper_amount": upper,
                "state": "HELD",
                "sent_at": None,
                "outcome": "PENDING",
                "latest_event_version": 0,
                "latest_event_id": None,
                "final_reported": False,
                "source_request_id": None,
            },
        )
        old = await rows(
            uow.connection,
            "budget_reservations",
            context.scope.channel_id,
            attempt_id=plan.attempt_id,
        )
        for reservation in old:
            await save(uow, "budget_reservations", reservation["id"], {"status": "RELEASED"})
        ids = []
        for policy, quantity in allocations:
            reservation_id = new_id("reservation")
            ids.append(reservation_id)
            await save(
                uow,
                "budget_reservations",
                reservation_id,
                {
                    "policy_id": policy["id"],
                    "policy_revision": policy["revision"],
                    "policy_version_id": policy["version_id"],
                    "period_start": period_start(now, policy["period"], policy["timezone"]),
                    "run_id": plan.run_id,
                    "attempt_id": plan.attempt_id,
                    "reserved_amount": quantity,
                    "settled_amount": None,
                    "currency": policy["currency"],
                    "status": "HELD",
                    "expires_at": now + timedelta(minutes=15),
                    "unit": policy["unit"],
                    "scope_snapshot": {
                        k: policy[k] for k in ("scope_type", "scope_id", "period", "timezone")
                    },
                },
            )
        await self.refresh_alerts(uow, now)
        return ReservationReceipt(
            scope=context.scope,
            run_id=plan.run_id,
            attempt_id=plan.attempt_id,
            reservation_ids=ids,
            state="HELD",
            upper_cost=Money(amount=upper, currency=row["currency"]) if upper is not None else None,
        )

    async def finish_admission(
        self, context: AuthContext, run_id: str, *, uow: UnitOfWork | None = None
    ) -> None:
        if uow is None:
            async with transaction(
                self.engine, context.scope, self.admission_keys(context, run_id)
            ) as work:
                await self.finish_admission(context, run_id, uow=work)
            return
        self.require(uow, context)
        uow.require_lock(platform_key())
        admission = await one(uow.connection, "admissions", context.scope.channel_id, run_id=run_id)
        if admission:
            self.verify_scope(admission, context.scope)
            if admission["status"] != "RELEASED":
                await save(uow, "admissions", admission["id"], {"status": "RELEASED"})
        for row in await rows(
            uow.connection,
            "platform_quota_occupancies",
            "system",
            target_channel_id=context.scope.channel_id,
            run_id=run_id,
        ):
            if row["status"] != "RELEASED":
                await save(
                    uow,
                    "platform_quota_occupancies",
                    row["id"],
                    {"status": "RELEASED"},
                    system=True,
                )
        await self.refresh_alerts(uow, utcnow())

    async def refresh_alerts(self, uow: UnitOfWork, now: datetime) -> None:
        policies = await self.policies(uow)
        exposure = await self.exposure_data(uow, policies, now)
        table = metadata.tables["budget_alerts"]
        starts = [period_start(now, p["period"], p["timezone"]) for p in policies]
        alerts = (
            [
                dict(r)
                for r in (
                    await uow.connection.execute(
                        select(table).where(
                            table.c.channel_id == uow.scope.channel_id,
                            table.c.rule_id.in_([p["id"] for p in policies]),
                            table.c.period_start >= min(starts),
                        )
                    )
                ).mappings()
            ]
            if starts
            else []
        )
        indexed_alerts = {
            (a["rule_id"], a["period_start"], a["threshold"], a["scope_key"]): a for a in alerts
        }
        if len(indexed_alerts) != len(alerts):
            raise ServiceError("STORAGE_INVARIANT_BROKEN", "预算提醒重复，请核查", 503)
        for policy in policies:
            try:
                used = await self.exposure(uow, policy, now, exposure)
            except ServiceError as exc:
                if exc.code != "BUDGET_PRICE_REQUIRED":
                    raise
                continue
            start = period_start(now, policy["period"], policy["timezone"])
            scope_key = digest([policy["scope_type"], policy["scope_id"]])
            for threshold_text in policy["thresholds"]:
                threshold = Decimal(threshold_text)
                active = used >= policy["limit_value"] * threshold
                alert = indexed_alerts.get((policy["id"], start, threshold, scope_key))
                if not alert and not active:
                    continue
                status = "ACTIVE" if active else "RESOLVED"
                if alert and alert["status"] == status:
                    continue
                transitions = (alert["transitions"] if alert else []) + [
                    {"status": status, "at": now.isoformat(), "used": str(used)}
                ]
                await save(
                    uow,
                    "budget_alerts",
                    alert["id"] if alert else new_id("alert"),
                    {
                        "rule_id": policy["id"],
                        "period_start": start,
                        "threshold": threshold,
                        "scope_key": scope_key,
                        "status": status,
                        "first_triggered_at": alert["first_triggered_at"] if alert else now,
                        "resolved_at": None if active else now,
                        "transitions": transitions,
                    },
                )

    async def reserve(
        self, uow: UnitOfWork, context: AuthContext, attempt_id: str
    ) -> BudgetReservation:
        """兼容 03 的单占用回执；必须先在同事务提交候选估算并完成全部策略预占。"""
        self.require(uow, context)
        row = await one(
            uow.connection, "usage_records", context.scope.channel_id, attempt_id=attempt_id
        )
        if row is None or row["state"] != "HELD":
            raise ServiceError(
                "BUDGET_ESTIMATE_REQUIRED", "请先使用实际候选参数调用 reserve_attempt", 409
            )
        self.verify_scope(row, context.scope)
        reservations = await rows(
            uow.connection,
            "budget_reservations",
            context.scope.channel_id,
            attempt_id=attempt_id,
            status="HELD",
        )
        if not reservations:
            raise ServiceError(
                "BUDGET_RESERVATION_NOT_REQUIRED", "此调用没有适用预算，请使用尝试组回执", 409
            )
        reservation = reservations[0]
        return BudgetReservation(
            reservation_id=reservation["id"],
            scope=context.scope,
            run_id=row["run_id"],
            attempt_id=attempt_id,
            policy_id=reservation["policy_id"],
            reserved=Money(amount=reservation["reserved_amount"], currency=reservation["currency"])
            if reservation["unit"] == "amount"
            else None,
            token_limit=int(reservation["reserved_amount"])
            if reservation["unit"] == "tokens"
            else None,
            state=reservation["status"],
            expires_at=reservation["expires_at"],
        )

    async def record_usage(self, uow: UnitOfWork, event: UsageEvent) -> None:
        from creativity_service.modules.usage.services import UsageService

        await UsageService(self.engine, self).settle(event, uow=uow)
