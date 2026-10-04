"""供应商账单按请求标识核查实际尝试，金额差异不覆盖原账本事实。"""

from collections import Counter
from decimal import Decimal
from typing import Any, Self

from pydantic import AwareDatetime, Field, field_validator, model_validator
from sqlalchemy import select

from creativity_service.core.auth.authentication import AdminSession
from creativity_service.core.database import Repository, transaction
from creativity_service.core.deletion import ContentRef, DeletionGuard, content_key
from creativity_service.core.locking import record_key
from creativity_service.core.primitives import (
    Contract,
    Identifier,
    ServiceError,
    canonical_json,
    digest,
    new_id,
    utcnow,
)
from creativity_service.modules.iam.audit import append_event
from creativity_service.modules.iam.operations_tables import metadata
from creativity_service.modules.iam.repositories import policy_key
from creativity_service.modules.integrations.automation import owner
from creativity_service.modules.models.tables import metadata as model_metadata
from creativity_service.modules.usage.management import UsageManagement
from creativity_service.modules.usage.query import visible
from creativity_service.modules.usage.tables import metadata as usage_metadata


class StatementLine(Contract):
    line_id: str = Field(min_length=1, max_length=128)
    request_id: str = Field(min_length=1, max_length=256)
    occurred_at: AwareDatetime
    amount: Decimal = Field(max_digits=24, decimal_places=8, allow_inf_nan=False)

    @field_validator("amount", mode="before")
    @classmethod
    def exact_amount(cls, value: Any) -> Any:
        if not isinstance(value, (str, Decimal)):
            raise ValueError("金额必须使用十进制字符串")
        return value


class StatementCreate(Contract):
    name: str = Field(min_length=1, max_length=128)
    version: str = Field(min_length=1, max_length=64)
    connection_id: Identifier
    currency: str = Field(pattern=r"^[A-Z]{3}$")
    start_at: AwareDatetime
    end_at: AwareDatetime
    lines: list[StatementLine] = Field(max_length=1000)

    @model_validator(mode="after")
    def bounds(self) -> Self:
        if self.start_at >= self.end_at or any(
            not self.start_at <= line.occurred_at < self.end_at for line in self.lines
        ):
            raise ValueError("账单行必须在核查时间范围内")
        if len(canonical_json(self.model_dump(mode="json"))) > 1048576:
            raise ValueError("账单输入超过一兆字节")
        return self


LABELS = {
    "MATCHED": "金额一致",
    "DIFFERENCE": "金额差异",
    "PLATFORM_MISSING": "平台记录缺失",
    "PROVIDER_MISSING": "供应商记录缺失",
    "DUPLICATE": "供应商重复行",
    "REQUEST_CONFLICT": "请求标识冲突",
    "CURRENCY_MISMATCH": "币种不同",
    "TIME_MISMATCH": "时间不符",
    "UNPRICED": "平台缺价",
    "PROVISIONAL": "平台金额未确认",
}


class Statements:
    def __init__(self, management: UsageManagement) -> None:
        self.management, self.engine = management, management.engine

    async def options(self, session: AdminSession) -> list[dict[str, str]]:
        context = await self.management.context(session)
        async with self.engine.connect() as connection:
            rows = await Repository(model_metadata.tables["model_connections"], context.scope).find(
                connection
            )
        return [{"connection_id": row["id"], "name": row["name"]} for row in rows]

    async def create(self, session: AdminSession, body: StatementCreate) -> dict[str, Any]:
        context = await self.management.context(session, "budget:manage")
        if body.connection_id not in {row["connection_id"] for row in await self.options(session)}:
            raise ServiceError("NOT_FOUND", "模型连接不存在或未获授权", 404)
        identifier = digest(
            [
                context.scope.model_dump(),
                owner(context),
                body.connection_id,
                body.name,
                body.version,
            ]
        )
        event_id = new_id("audit")
        repository = Repository(metadata.tables["provider_statements"], context.scope)
        async with transaction(
            self.engine,
            context.scope,
            [
                content_key(context.scope),
                policy_key("system"),
                policy_key(context.scope.channel_id),
                record_key(context.scope.channel_id, "provider_statements", identifier),
                record_key(context.scope.channel_id, "audit_events", event_id),
            ],
        ) as uow:
            await self.management.channels.locked(uow, session, "budget:manage")
            await DeletionGuard(context.scope).check(
                uow, [ContentRef("provider_statement", identifier)]
            )
            previous = await repository.get(uow.connection, identifier)
            source_digest = digest(body.model_dump(mode="json"))
            if previous:
                if previous["source_digest"] != source_digest:
                    raise ServiceError(
                        "STATEMENT_VERSION_CONFLICT", "同一来源版本的账单内容不同", 409
                    )
            else:
                await repository.add(
                    uow,
                    identifier,
                    {
                        **body.model_dump(exclude={"lines"}),
                        "lines": [line.model_dump(mode="json") for line in body.lines],
                        "source_digest": source_digest,
                        "owner_key": owner(context),
                    },
                )
                await append_event(
                    uow,
                    event_id,
                    context.actor_id or "",
                    context.request_id,
                    "usage:statement",
                    "provider_statement",
                    identifier,
                )
        return await self.detail(session, identifier)

    async def list(self, session: AdminSession) -> list[dict[str, Any]]:
        context = await self.management.context(session)
        async with self.engine.connect() as connection:
            repo = Repository(metadata.tables["provider_statements"], context.scope)
            table = repo.table
            rows = (
                (
                    await connection.execute(
                        select(table)
                        .where(repo.predicate(), table.c.owner_key == owner(context))
                        .order_by(table.c.created_at.desc(), table.c.id.desc())
                        .limit(100)
                    )
                )
                .mappings()
                .all()
            )
        return [
            {
                k: row[k]
                for k in (
                    "id",
                    "name",
                    "version",
                    "currency",
                    "start_at",
                    "end_at",
                    "created_at",
                    "source_digest",
                )
            }
            for row in rows
        ]

    async def detail(self, session: AdminSession, identifier: str) -> dict[str, Any]:
        context = await self.management.context(session)
        scopes = [
            scope
            for scope in await self.management.scopes(session)
            if scope.environment == context.scope.environment
            and scope.data_scope_id == context.scope.data_scope_id
        ]
        async with transaction(self.engine, context.scope, [content_key(context.scope)]) as uow:
            await DeletionGuard(context.scope).check(
                uow, [ContentRef("provider_statement", identifier)]
            )
            statement = await Repository(metadata.tables["provider_statements"], context.scope).get(
                uow.connection, identifier
            )
            if not statement or statement["owner_key"] != owner(context):
                raise ServiceError("NOT_FOUND", "当前身份没有此账单", 404)
            table = usage_metadata.tables["usage_records"]
            records = [
                dict(r)
                for r in (
                    await uow.connection.execute(
                        select(table)
                        .where(
                            table.c.channel_id == context.scope.channel_id,
                            table.c.environment == context.scope.environment,
                            table.c.data_scope_id == context.scope.data_scope_id,
                            *(
                                [
                                    table.c.subject_type == context.scope.subject_type,
                                    table.c.subject_id == context.scope.subject_id,
                                ]
                                if context.scope.subject_id
                                else []
                            ),
                            table.c.connection_id == statement["connection_id"],
                            table.c.sent_at >= statement["start_at"],
                            table.c.sent_at < statement["end_at"],
                        )
                        .limit(10001)
                    )
                ).mappings()
                if visible(dict(r), scopes)
            ]
        if len(records) > 10000:
            raise ServiceError("STATEMENT_RANGE_TOO_LARGE", "实际尝试过多，请缩小核查时间范围", 422)
        lines = [StatementLine.model_validate(v) for v in statement["lines"]]
        identifiers = Counter(line.line_id for line in lines)
        requests = Counter(line.request_id for line in lines)
        used: set[str] = set()
        results: list[dict[str, Any]] = []
        for line in lines:
            candidates = [r for r in records if r["source_request_id"] == line.request_id]
            used.update(r["id"] for r in candidates)
            current = candidates[0] if len(candidates) == 1 else None
            if identifiers[line.line_id] > 1 or requests[line.request_id] > 1:
                state = "DUPLICATE"
            elif not candidates:
                state = "PLATFORM_MISSING"
            elif len(candidates) != 1:
                state = "REQUEST_CONFLICT"
            elif current and current["currency"] and current["currency"] != statement["currency"]:
                state = "CURRENCY_MISMATCH"
            elif current and abs((current["sent_at"] - line.occurred_at).total_seconds()) > 300:
                state = "TIME_MISMATCH"
            elif current and (current["amount"] is None or current["pricing_status"] == "UNPRICED"):
                state = "UNPRICED"
            elif current and (
                current["pricing_status"] != "PRICED" or current["usage_status"] != "REPORTED"
            ):
                state = "PROVISIONAL"
            else:
                state = "MATCHED" if current and current["amount"] == line.amount else "DIFFERENCE"
            results.append(self.result(state, current, line, statement))
        for row in records:
            if row["id"] not in used and row["currency"] in {None, statement["currency"]}:
                results.append(self.result("PROVIDER_MISSING", row, None, statement))
        return {
            **{
                k: statement[k]
                for k in (
                    "id",
                    "name",
                    "version",
                    "source_digest",
                    "currency",
                    "start_at",
                    "end_at",
                )
            },
            "checked_at": utcnow().isoformat(),
            "results": results,
            "counts": [
                {
                    "state": code,
                    "label": LABELS[code],
                    "count": sum(r["state"] == code for r in results),
                }
                for code in LABELS
            ],
        }

    @staticmethod
    def result(
        state: str,
        row: dict[str, Any] | None,
        line: StatementLine | None,
        statement: dict[str, Any],
    ) -> dict[str, Any]:
        comparable = (
            row and row["amount"] is not None and row["currency"] == statement["currency"] and line
        )
        return {
            "state": state,
            "state_label": LABELS[state],
            "line_id": line.line_id if line else None,
            "request_id": line.request_id if line else row["source_request_id"] if row else None,
            "run_id": row["run_id"] if row else None,
            "model_name": row["snapshot"].get("names", {}).get("model") if row else None,
            "provider_amount": format(line.amount, "f") if line else None,
            "platform_amount": format(row["amount"], "f")
            if row and row["amount"] is not None
            else None,
            "platform_currency": row["currency"] if row else None,
            "difference": format(line.amount - row["amount"], "f")
            if comparable and row and line
            else None,
            "late_reported": bool(
                row and row["final_reported"] and row["updated_at"] > statement["created_at"]
            ),
        }
