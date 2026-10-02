"""账本单源查询；普通查询只在已授权的数据域和主体内计算。"""

from decimal import Decimal
from typing import Any

from sqlalchemy import and_, or_, select
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine

from creativity_service.core.context import Scope
from creativity_service.core.database import transaction
from creativity_service.core.primitives import Money, ServiceError, digest, utcnow
from creativity_service.modules.channels.repositories import required as channel_required
from creativity_service.modules.channels.schemas import UsageQuery, UsageView
from creativity_service.modules.usage.pricing import PRECISION, timezone
from creativity_service.modules.usage.repositories import ledger_key, rows, save
from creativity_service.modules.usage.schemas import (
    CurrencyTotal,
    RecordView,
    UsageFilter,
    UsageSummary,
)
from creativity_service.modules.usage.tables import metadata

USAGE_LABELS = {"REPORTED": "供应商已上报", "ESTIMATED": "估算", "MISSING": "用量缺失"}
PRICING_LABELS = {"PRICED": "已计价", "PROVISIONAL": "暂估", "UNPRICED": "未定价"}
PURPOSE_LABELS = {"production": "正式调用", "debug": "调试", "evaluation": "评测"}
STATE_LABELS = {"HELD": "尚未发送", "PENDING": "待核实", "SETTLED": "已结算", "RELEASED": "已释放"}
OUTCOME_LABELS = {
    "PENDING": "进行中",
    "SUCCEEDED": "成功",
    "FAILED": "失败",
    "UNKNOWN": "结果待核实",
}


def validate_filter(query: UsageFilter) -> None:
    timezone(query.timezone)
    if query.start_at >= query.end_at:
        raise ServiceError("VALIDATION_ERROR", "结束时间须晚于开始时间", 422)
    if (query.subject_type is None) != (query.subject_id is None):
        raise ServiceError("VALIDATION_ERROR", "主体类型与编号须同时填写", 422)


def visible(row: dict[str, Any], scopes: list[Scope]) -> bool:
    return any(
        row["channel_id"] == s.channel_id
        and row["environment"] == s.environment
        and row["data_scope_id"] == s.data_scope_id
        and (
            s.subject_id is None
            or (row["subject_type"], row["subject_id"]) == (s.subject_type, s.subject_id)
        )
        for s in scopes
    )


def matches_filter(row: dict[str, Any], query: UsageFilter) -> bool:
    snapshot = row.get("snapshot", {})
    for name, value in query.model_dump().items():
        if name in {"start_at", "end_at", "timezone", "target_currency"} or value is None:
            continue
        if row.get(name, snapshot.get(name)) != value:
            return False
    return bool(query.start_at <= row["created_at"] < query.end_at)


def view(row: dict[str, Any]) -> RecordView:
    return RecordView(
        **{
            k: row[k]
            for k in (
                "id",
                "channel_id",
                "run_id",
                "attempt_id",
                "created_at",
                "purpose",
                "input_tokens",
                "output_tokens",
                "cached_tokens",
                "reasoning_tokens",
                "usage_status",
                "pricing_status",
                "currency",
                "state",
                "revision",
                "normalized_tokens",
                "subset_relations",
                "calculation",
            )
        },
        names=row["snapshot"].get("names", {}),
        purpose_label=PURPOSE_LABELS[row["purpose"]],
        usage_label=USAGE_LABELS[row["usage_status"]],
        pricing_label=PRICING_LABELS[row["pricing_status"]],
        state_label=STATE_LABELS[row["state"]],
        outcome_label=OUTCOME_LABELS[row["outcome"]],
        amount=format(row["amount"], "f") if row["amount"] is not None else None,
    )


def totals(
    records: list[dict[str, Any]], admissions: list[dict[str, Any]], zone: str
) -> UsageSummary:
    actual = [r for r in records if r["sent_at"] is not None]
    currencies: dict[str, dict[str, Decimal]] = {}
    trend: dict[str, dict[str, Any]] = {}
    for row in actual:
        date = row["created_at"].astimezone(timezone(zone)).strftime("%Y-%m-%d")
        bucket = trend.setdefault(
            date,
            {"date": date, "attempts": 0, "input_tokens": None, "output_tokens": None, "costs": {}},
        )
        bucket["attempts"] += 1
        for field in ("input_tokens", "output_tokens"):
            if row[field] is not None:
                bucket[field] = (bucket[field] or 0) + row[field]
        if row["amount"] is not None and row["currency"]:
            key = "priced" if row["pricing_status"] == "PRICED" else "provisional"
            amounts = currencies.setdefault(
                row["currency"], {"priced": Decimal(0), "provisional": Decimal(0)}
            )
            amounts[key] += row["amount"]
            daily = bucket["costs"].setdefault(row["currency"], {"priced": "0", "provisional": "0"})
            daily[key] = str(Decimal(daily[key]) + row["amount"])
    resolved = [r for r in actual if r["outcome"] in {"SUCCEEDED", "FAILED"}]

    def count(field: str) -> int | None:
        values = [r[field] for r in actual if r[field] is not None]
        return sum(values) if values else None

    watermark = max((r["updated_at"] for r in records), default=None)
    unpriced = sum(r["pricing_status"] == "UNPRICED" for r in actual)
    return UsageSummary(
        requests=len({a["run_id"] for a in admissions} | {r["run_id"] for r in actual}),
        attempts=len(actual),
        success_rate=str(
            (Decimal(sum(r["outcome"] == "SUCCEEDED" for r in resolved)) / len(resolved)).quantize(
                Decimal("0.0001")
            )
        )
        if resolved
        else None,
        input_tokens=count("input_tokens"),
        output_tokens=count("output_tokens"),
        missing_usage=sum(r["usage_status"] == "MISSING" for r in actual),
        unpriced=unpriced,
        costs=[
            CurrencyTotal(
                currency=currency,
                priced=format(amounts["priced"], "f"),
                provisional=format(amounts["provisional"], "f"),
            )
            for currency, amounts in sorted(currencies.items())
        ],
        trend=[trend[d] for d in sorted(trend)],
        aggregate_updated_at=utcnow(),
        ledger_watermark=watermark,
        price_complete=unpriced == 0 and all(r["pricing_status"] == "PRICED" for r in actual),
        timezone=zone,
    )


class UsageQueries:
    def __init__(self, engine: AsyncEngine) -> None:
        self.engine = engine

    async def select(
        self,
        connection: AsyncConnection,
        channel_id: str,
        scopes: list[Scope],
        query: UsageFilter,
        table: str = "usage_records",
    ) -> list[dict[str, Any]]:
        validate_filter(query)
        if not scopes or any(s.channel_id != channel_id for s in scopes):
            raise ServiceError("FORBIDDEN", "缺少明确的用量查询权限", 403)
        definition = metadata.tables[table]
        predicates = [
            definition.c.channel_id == channel_id,
            definition.c.created_at >= query.start_at,
            definition.c.created_at < query.end_at,
        ]
        predicates.append(
            or_(
                *(
                    and_(
                        definition.c.environment == scope.environment,
                        definition.c.data_scope_id == scope.data_scope_id,
                        *(
                            [
                                definition.c.subject_type == scope.subject_type,
                                definition.c.subject_id == scope.subject_id,
                            ]
                            if scope.subject_id
                            else []
                        ),
                    )
                    for scope in scopes
                )
            )
        )
        for key, value in query.model_dump().items():
            if key in {"start_at", "end_at", "timezone", "target_currency"} or value is None:
                continue
            column = definition.c[key] if key in definition.c else definition.c.snapshot[key].astext
            predicates.append(column == value)
        result = await connection.execute(select(definition).where(*predicates))
        return [dict(row) for row in result.mappings()]

    async def summary(
        self, channel_id: str, scopes: list[Scope], query: UsageFilter
    ) -> UsageSummary:
        async with self.engine.connect() as connection:
            # 同一快照读取账本与准入，避免两次读之间迟报导致统计互相矛盾。
            async with connection.begin():
                await connection.exec_driver_sql("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ")
                records = await self.select(connection, channel_id, scopes, query)
                admissions = await self.select(connection, channel_id, scopes, query, "admissions")
                summary = totals(records, admissions, query.timezone)
                if query.target_currency:
                    rates = await rows(connection, "usage_exchange_rates", channel_id)
                    costs = []
                    for cost in summary.costs:
                        available = [
                            r
                            for r in rates
                            if r["base_currency"] == cost.currency
                            and r["quote_currency"] == query.target_currency
                            and r["effective_at"] <= query.end_at
                        ]
                        rate = (
                            max(available, key=lambda r: (r["effective_at"], r["created_at"]))
                            if available
                            else None
                        )
                        conversion = {
                            "currency": query.target_currency,
                            "amount": None,
                            "source": None,
                            "date": None,
                            "rate": None,
                        }
                        if rate:
                            conversion.update(
                                amount=str(
                                    (
                                        (Decimal(cost.priced) + Decimal(cost.provisional))
                                        * rate["rate"]
                                    ).quantize(PRECISION)
                                ),
                                source=rate["source"],
                                date=rate["effective_at"].isoformat(),
                                rate=str(rate["rate"]),
                            )
                        costs.append(cost.model_copy(update={"conversion": conversion}))
                    summary = summary.model_copy(update={"costs": costs})
        return summary

    async def query(self, channel_id: str, scopes: list[Scope], query: UsageQuery) -> UsageView:
        result = await self.summary(
            channel_id, scopes, UsageFilter(start_at=query.start_at, end_at=query.end_at)
        )
        async with self.engine.connect() as connection:
            channel = await channel_required(connection, "channels", channel_id, id=channel_id)
        return UsageView(
            channel_id=channel_id,
            channel_name=channel["name"],
            start_at=query.start_at,
            end_at=query.end_at,
            calls=result.attempts,
            requests=result.requests,
            missing_usage=result.missing_usage,
            unpriced=result.unpriced,
            price_complete=result.price_complete,
            aggregate_updated_at=result.aggregate_updated_at,
            provisional_costs=[
                Money(amount=Decimal(c.provisional), currency=c.currency) for c in result.costs
            ],
            input_tokens=result.input_tokens,
            output_tokens=result.output_tokens,
            costs=[Money(amount=Decimal(c.priced), currency=c.currency) for c in result.costs],
        )

    async def rebuild(self, scope: Scope, scopes: list[Scope], query: UsageFilter) -> UsageSummary:
        async with transaction(self.engine, scope, [ledger_key(scope.channel_id)]) as uow:
            records = await self.select(uow.connection, scope.channel_id, scopes, query)
            admissions = await self.select(
                uow.connection, scope.channel_id, scopes, query, "admissions"
            )
            summary = totals(records, admissions, query.timezone)
            dimensions = {
                "filters": query.model_dump(mode="json"),
                "scopes": [s.model_dump(mode="json") for s in scopes],
            }
            key = digest(dimensions)
            await save(
                uow,
                "usage_aggregates",
                key,
                {
                    "dimensions": dimensions,
                    "dimensions_digest": key,
                    "period_start": query.start_at,
                    "currency": None,
                    "totals": summary.model_dump(mode="json"),
                    "ledger_watermark": summary.ledger_watermark or summary.aggregate_updated_at,
                },
            )
        return summary
