"""管理端授权、价格版本、预算版本与核算维护。"""

from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine

from creativity_service.core.auth.authentication import AdminSession
from creativity_service.core.context import AuthContext, ControlScope, Scope
from creativity_service.core.database import Repository, transaction
from creativity_service.core.database.tables import metadata as core_metadata
from creativity_service.core.deletion import ContentRef, DeletionGuard, content_key
from creativity_service.core.locking import record_key
from creativity_service.core.primitives import ServiceError, digest, new_id, utcnow
from creativity_service.modules.budgets.schemas import (
    BudgetCreate,
    BudgetUpdate,
    BudgetView,
    PlatformLimitCreate,
)
from creativity_service.modules.budgets.services import BudgetService
from creativity_service.modules.channels.reading import ChannelReadData
from creativity_service.modules.channels.repositories import one as channel_one
from creativity_service.modules.channels.repositories import rows as channel_rows
from creativity_service.modules.channels.services import ChannelService
from creativity_service.modules.iam.accounts import current_actor
from creativity_service.modules.iam.audit import append_event
from creativity_service.modules.iam.authorization import require_platform
from creativity_service.modules.iam.repositories import policy_key
from creativity_service.modules.usage.pricing import timezone, validate_price
from creativity_service.modules.usage.query import UsageQueries, view, visible
from creativity_service.modules.usage.repositories import (
    ledger_key,
    one,
    platform_key,
    required,
    rows,
    save,
)
from creativity_service.modules.usage.schemas import (
    ExchangeRateCreate,
    PriceCreate,
    PriceVersionView,
    RecordDetail,
    RecordPage,
    UsageFilter,
)
from creativity_service.modules.usage.services import UsageService

UNIT_LABELS = {
    "amount": "金额",
    "tokens": "Token 数",
    "attempts": "调用次数",
    "requests": "请求数",
    "concurrency": "并发数",
}


class UsageManagement:
    def __init__(
        self,
        engine: AsyncEngine,
        channels: ChannelService,
        budgets: BudgetService,
        usage: UsageService,
        queries: UsageQueries,
    ) -> None:
        self.engine, self.channels, self.budgets, self.usage, self.queries = (
            engine,
            channels,
            budgets,
            usage,
            queries,
        )

    async def context(self, session: AdminSession, action: str = "usage:read") -> AuthContext:
        if not isinstance(session.context, AuthContext):
            raise ServiceError("FORBIDDEN", "请先进入获授权的业务渠道", 403)
        await self.channels.authorize(session, session.context.scope.channel_id, action)
        return session.context

    async def scopes(self, session: AdminSession, action: str = "usage:read") -> list[Scope]:
        context = await self.context(session, action)
        result = []
        async with self.engine.connect() as connection:
            data = await ChannelReadData.load(connection, session, context.scope.channel_id)
            for domain in data.domains.values():
                if data.visible(domain["environment"], [domain["id"]], action=action):
                    if context.scope.subject_id and domain["id"] != context.scope.data_scope_id:
                        continue
                    result.append(
                        Scope(
                            channel_id=context.scope.channel_id,
                            environment=domain["environment"],
                            data_scope_id=domain["id"],
                            subject_type=context.scope.subject_type,
                            subject_id=context.scope.subject_id,
                        )
                    )
        if not result:
            raise ServiceError("FORBIDDEN", "没有可查询的用量范围", 403)
        return result

    async def records(
        self, session: AdminSession, query: UsageFilter, offset: int = 0, limit: int = 50
    ) -> RecordPage:
        scopes = await self.scopes(session)
        if offset < 0 or not 1 <= limit <= 200:
            raise ServiceError("VALIDATION_ERROR", "分页范围无效", 422)
        async with self.engine.connect() as connection:
            selected = await self.queries.select(connection, scopes[0].channel_id, scopes, query)
        selected = sorted(selected, key=lambda r: (r["created_at"], r["id"]), reverse=True)
        return RecordPage(
            items=[view(r) for r in selected[offset : offset + limit]],
            total=len(selected),
            offset=offset,
            limit=limit,
        )

    async def detail(self, session: AdminSession, usage_id: str) -> RecordDetail:
        scopes = await self.scopes(session)
        async with self.engine.connect() as connection:
            row = await required(connection, "usage_records", scopes[0].channel_id, id=usage_id)
            if not visible(row, scopes):
                raise ServiceError("NOT_FOUND", "请求资源不存在", 404)
            adjustments = await rows(
                connection, "usage_adjustments", scopes[0].channel_id, usage_id=usage_id
            )
            events = await rows(
                connection, "usage_events", scopes[0].channel_id, attempt_id=row["attempt_id"]
            )
        return RecordDetail(
            **view(row).model_dump(),
            source={
                **row["snapshot"],
                "connection_id": row["connection_id"],
                "source_request_id": row["source_request_id"],
            },
            adjustments=adjustments,
            events=events,
        )

    async def model(
        self, connection: AsyncConnection, channel_id: str, model_id: str
    ) -> dict[str, Any]:
        from creativity_service.modules.models.tables import metadata

        table = metadata.tables["models"]
        found = (
            (
                await connection.execute(
                    select(table).where(table.c.channel_id == channel_id, table.c.id == model_id)
                )
            )
            .mappings()
            .all()
        )
        if len(found) != 1:
            raise ServiceError("NOT_FOUND", "请求模型不存在", 404)
        return dict(found[0])

    @staticmethod
    def price_view(row: dict[str, Any]) -> PriceVersionView:
        return PriceVersionView(
            id=row["id"],
            model_id=row["model_id"],
            name=row["name"],
            currency=row["currency"],
            items=row["price_items"],
            subset_relations=row["subset_relations"],
            effective_at=row["effective_at"],
            source=row["source"],
            created_at=row["created_at"],
        )

    async def prices(
        self, session: AdminSession, model_id: str | None = None
    ) -> list[PriceVersionView]:
        context = await self.context(session)
        async with self.engine.connect() as connection:
            if model_id:
                await self.model(connection, context.scope.channel_id, model_id)
            result = await rows(
                connection,
                "price_versions",
                context.scope.channel_id,
                **({"model_id": model_id} if model_id else {}),
            )
        return [
            self.price_view(r)
            for r in sorted(result, key=lambda r: r["effective_at"], reverse=True)
        ]

    async def create_price(
        self, session: AdminSession, model_id: str, body: PriceCreate
    ) -> PriceVersionView:
        context = await self.context(session, "model:manage")
        validate_price(body)
        price_id, audit_id = new_id("price"), new_id("audit")
        keys = [
            ledger_key(context.scope.channel_id),
            policy_key(context.scope.channel_id),
            policy_key("system"),
            content_key(context.scope),
            record_key(context.scope.channel_id, "audit_events", audit_id),
        ]
        async with transaction(self.engine, context.scope, keys) as uow:
            await self.channels.locked(uow, session, "model:manage")
            await self.model(uow.connection, context.scope.channel_id, model_id)
            await DeletionGuard(context.scope).check(uow, [ContentRef("model", model_id)])
            row = await save(
                uow,
                "price_versions",
                price_id,
                {
                    "model_id": model_id,
                    "name": body.name.strip(),
                    "currency": body.currency,
                    "price_items": [p.model_dump(mode="json") for p in body.items],
                    "subset_relations": body.subset_relations,
                    "unit": "按维度配置",
                    "effective_at": body.effective_at,
                    "source": body.source.strip(),
                },
            )
            await append_event(
                uow,
                audit_id,
                session.account.id,
                context.request_id,
                "model:manage",
                "model",
                model_id,
            )
        return self.price_view(row)

    async def budget_name(self, connection: AsyncConnection, policy: dict[str, Any]) -> str | None:
        channel_id, kind = policy["channel_id"], policy["scope_type"]
        if kind == "model":
            return str((await self.model(connection, channel_id, policy["scope_id"]))["name"])
        row = await channel_one(
            connection,
            "channels" if kind == "channel" else "channel_keys",
            channel_id,
            id=policy["scope_id"],
        )
        return row["name"] if row else None

    async def budgets_list(self, session: AdminSession) -> list[BudgetView]:
        context = await self.context(session, "budget:manage")
        async with transaction(
            self.engine, context.scope, [ledger_key(context.scope.channel_id)]
        ) as uow:
            data = await ChannelReadData.load(uow.connection, session, context.scope.channel_id)
            for domain in data.domains.values():
                if not data.visible(domain["environment"], [domain["id"]], action="budget:manage"):
                    raise ServiceError("NOT_FOUND", "请求资源不在授权范围内", 404)
            policies = await rows(uow.connection, "budget_policies", context.scope.channel_id)
            now = utcnow()
            exposure = await self.budgets.exposure_data(uow, policies, now)
            from creativity_service.modules.channels.tables import metadata as channels
            from creativity_service.modules.models.tables import metadata as models

            names = {}
            for kind, table in (
                ("model", models.tables["models"]),
                ("channel", channels.tables["channels"]),
                ("key", channels.tables["channel_keys"]),
            ):
                identifiers = {p["scope_id"] for p in policies if p["scope_type"] == kind}
                found = (
                    [
                        dict(r)
                        for r in (
                            await uow.connection.execute(
                                select(table.c.id, table.c.name).where(
                                    table.c.channel_id == context.scope.channel_id,
                                    table.c.id.in_(identifiers),
                                )
                            )
                        ).mappings()
                    ]
                    if identifiers
                    else []
                )
                for r in found:
                    names[(kind, r["id"])] = r["name"]
                if kind == "model" and identifiers - {r["id"] for r in found}:
                    raise ServiceError("NOT_FOUND", "请求模型不存在", 404)
            result = []
            for row in policies:
                blocked_reason = None
                used: Decimal | None = None
                try:
                    used = await self.budgets.exposure(uow, row, now, exposure)
                except ServiceError as exc:
                    if exc.code != "BUDGET_PRICE_REQUIRED":
                        raise
                    blocked_reason = exc.message
                result.append(
                    BudgetView(
                        **{k: row[k] for k in BudgetCreate.model_fields},
                        id=row["id"],
                        version_id=row["version_id"],
                        revision=row["revision"],
                        mode_label="超额阻断" if row["mode"] == "HARD" else "仅提醒",
                        unit_label=UNIT_LABELS[row["unit"]],
                        scope_name=names.get((row["scope_type"], row["scope_id"])),
                        used=format(used, "f") if used is not None else None,
                        remaining=format(max(Decimal(0), row["limit_value"] - used), "f")
                        if used is not None
                        else None,
                        blocked_reason=blocked_reason,
                    )
                )
            return result

    async def save_budget(
        self, session: AdminSession, body: BudgetCreate | BudgetUpdate, policy_id: str | None = None
    ) -> BudgetView:
        context = await self.context(session, "budget:manage")
        timezone(body.timezone)
        if (
            not body.name.strip()
            or body.limit_value <= 0
            or not body.thresholds
            or len(set(body.thresholds)) != len(body.thresholds)
            or any(not t.is_finite() or not 0 < t <= 1 for t in body.thresholds)
        ):
            raise ServiceError(
                "BUDGET_INVALID", "预算名称、正数额度及 0 至 100% 提醒阈值必须有效", 422
            )
        if (body.unit == "amount") != (body.currency is not None):
            raise ServiceError("BUDGET_INVALID", "仅金额预算必须指定币种", 422)
        if body.unit != "amount" and body.limit_value != body.limit_value.to_integral_value():
            raise ServiceError("BUDGET_INVALID", "数量限额必须为整数", 422)
        policy_id = policy_id or new_id("budget")
        version_id, audit_id, link_id = new_id("version"), new_id("audit"), new_id("source")
        keys = [
            ledger_key(context.scope.channel_id),
            policy_key(context.scope.channel_id),
            policy_key("system"),
            content_key(context.scope),
            record_key(context.scope.channel_id, "resource_versions", version_id),
            record_key(context.scope.channel_id, "source_links", link_id),
            record_key(context.scope.channel_id, "audit_events", audit_id),
        ]
        async with transaction(self.engine, context.scope, keys) as uow:
            await self.channels.locked(uow, session, "budget:manage")
            # 渠道总额影响所有业务域，只有覆盖全部范围的管理人可以修改。
            for domain in await channel_rows(
                uow.connection, "data_scopes", context.scope.channel_id
            ):
                await self.channels.require_visible(
                    uow.connection,
                    session,
                    domain["environment"],
                    [domain["id"]],
                    action="budget:manage",
                )
            old = await one(
                uow.connection, "budget_policies", context.scope.channel_id, id=policy_id
            )
            if (old is None and isinstance(body, BudgetUpdate)) or (
                old and (not isinstance(body, BudgetUpdate) or old["revision"] != body.revision)
            ):
                raise ServiceError("REVISION_CONFLICT", "预算已变更，请刷新后重试", 409)
            if body.scope_type == "channel" and body.scope_id != context.scope.channel_id:
                raise ServiceError("NOT_FOUND", "请求渠道不存在", 404)
            if body.scope_type == "key" and not await channel_one(
                uow.connection, "channel_keys", context.scope.channel_id, id=body.scope_id
            ):
                raise ServiceError("NOT_FOUND", "请求凭据不存在", 404)
            if body.scope_type == "model":
                await self.model(uow.connection, context.scope.channel_id, body.scope_id)
            policies = [
                p
                for p in await rows(uow.connection, "budget_policies", context.scope.channel_id)
                if p["id"] != policy_id
            ]
            candidate = {**body.model_dump(exclude={"revision"}), "id": policy_id}
            policies.append(candidate)
            self.validate_allocations(policies)
            guard = DeletionGuard(context.scope)
            await guard.check(uow, [ContentRef("budget_policy", policy_id)])
            payload = body.model_dump(mode="json", exclude={"revision"})
            await Repository(core_metadata.tables["resource_versions"], context.scope).add(
                uow,
                version_id,
                {
                    "resource_type": "budget_policy",
                    "resource_id": policy_id,
                    "version_label": f"预算版本 {old['revision'] + 1 if old else 1}",
                    "state": "FROZEN",
                    "content": payload,
                    "content_digest": digest({"content": payload, "output_schema": {}}),
                    "dependencies": [],
                    "dependencies_digest": digest([]),
                    "output_schema": {},
                    "created_by": session.account.id,
                },
            )
            await guard.link(
                uow,
                link_id,
                ContentRef("budget_policy", policy_id),
                ContentRef("version", version_id),
            )
            await save(
                uow,
                "budget_policies",
                policy_id,
                {
                    **body.model_dump(exclude={"revision", "thresholds"}),
                    "thresholds": [str(t) for t in sorted(body.thresholds)],
                    "version_id": version_id,
                },
            )
            await append_event(
                uow,
                audit_id,
                session.account.id,
                context.request_id,
                "budget:manage",
                "budget_policy",
                policy_id,
            )
            await self.budgets.refresh_alerts(uow, utcnow())
        return next(p for p in await self.budgets_list(session) if p.id == policy_id)

    @staticmethod
    def validate_allocations(policies: list[dict[str, Any]]) -> None:
        active = [p for p in policies if p["status"] == "ACTIVE" and p["mode"] == "HARD"]
        for child in (p for p in active if p["scope_type"] != "channel"):
            parents = [
                p
                for p in active
                if p["scope_type"] == "channel"
                and all(p[k] == child[k] for k in ("unit", "currency", "period", "timezone"))
            ]
            if not parents:
                raise ServiceError("BUDGET_PARENT_REQUIRED", "请先配置同口径的渠道总预算", 422)
            allocated = sum(
                p["limit_value"]
                for p in active
                if p["scope_type"] == child["scope_type"]
                and all(p[k] == child[k] for k in ("unit", "currency", "period", "timezone"))
            )
            if any(allocated > p["limit_value"] for p in parents):
                raise ServiceError("BUDGET_ALLOCATION_EXCEEDED", "子预算分配超过渠道总额度", 422)

    async def alerts(self, session: AdminSession) -> list[dict[str, Any]]:
        context = await self.context(session, "budget:manage")
        async with self.engine.connect() as connection:
            policies = {
                p["id"]: p
                for p in await rows(connection, "budget_policies", context.scope.channel_id)
            }
            return [
                {
                    **r,
                    "name": policies[r["rule_id"]]["name"],
                    "status_label": "已触发" if r["status"] == "ACTIVE" else "已解除",
                }
                for r in await rows(connection, "budget_alerts", context.scope.channel_id)
            ]

    async def exchange_rate(
        self, session: AdminSession, body: ExchangeRateCreate
    ) -> dict[str, Any]:
        context = await self.context(session, "budget:manage")
        if body.rate <= 0 or body.base_currency == body.quote_currency:
            raise ServiceError("VALIDATION_ERROR", "汇率须为正数且币种不同", 422)
        async with transaction(
            self.engine,
            context.scope,
            [
                ledger_key(context.scope.channel_id),
                policy_key(context.scope.channel_id),
                policy_key("system"),
            ],
        ) as uow:
            await self.channels.locked(uow, session, "budget:manage")
            return await save(uow, "usage_exchange_rates", new_id("fx"), body.model_dump())

    async def platform_limits(
        self, session: AdminSession, body: PlatformLimitCreate | None = None
    ) -> list[dict[str, Any]]:
        await self.channels.iam.authentication.revalidate_admin(session, governance=True)
        if isinstance(session.context, AuthContext):
            raise ServiceError("FORBIDDEN", "平台限额须使用平台管理会话", 403)
        require_platform(session.account, "channel:govern")
        if body:
            timezone(body.timezone)
        scope = ControlScope(purpose="platform_limits", actor_id=session.account.id)
        async with transaction(self.engine, scope, [platform_key(), policy_key("system")]) as uow:
            await current_actor(uow, session, "channel:govern")
            records = await rows(uow.connection, "platform_limits", "system")
            if body:
                versions = [r for r in records if r["limit_code"] == body.limit_code]
                latest = max(versions, key=lambda r: r["created_at"]) if versions else None
                if (latest and body.revision != len(versions)) or (
                    not latest and body.revision is not None
                ):
                    raise ServiceError("REVISION_CONFLICT", "平台限额已更新，请刷新后重试", 409)
                await save(
                    uow,
                    "platform_limits",
                    new_id("limit"),
                    {
                        **body.model_dump(exclude={"revision", "limit_value"}),
                        "limit_value": Decimal(body.limit_value),
                        "kind": body.unit,
                        "replaces_id": latest["id"] if latest else None,
                        "effective_at": utcnow(),
                    },
                    system=True,
                )
                records = await rows(uow.connection, "platform_limits", "system")
            return records

    async def options(self, session: AdminSession) -> dict[str, Any]:
        scopes = await self.scopes(session)
        channel_id = scopes[0].channel_id
        from creativity_service.modules.models.tables import metadata as models_metadata

        async with self.engine.connect() as connection:
            models = (
                (
                    await connection.execute(
                        select(models_metadata.tables["models"]).where(
                            models_metadata.tables["models"].c.channel_id == channel_id
                        )
                    )
                )
                .mappings()
                .all()
            )
            keys = await channel_rows(connection, "channel_keys", channel_id)
            domains = await channel_rows(connection, "data_scopes", channel_id)
            records = await rows(connection, "usage_records", channel_id)
        allowed = [r for r in records if visible(r, scopes)]

        def options(kind: str) -> list[dict[str, str]]:
            values = {
                r[f"{kind}_id"]: r["snapshot"].get("names", {}).get(kind)
                for r in allowed
                if r.get(f"{kind}_id")
            }
            return [{"value": key, "label": value} for key, value in values.items() if value]

        return {
            "models": [{"value": r["id"], "label": r["name"]} for r in models],
            "keys": [
                {"value": r["id"], "label": r["name"]}
                for r in keys
                if any(s.environment == r["environment"] for s in scopes)
            ],
            "data_scopes": [
                {"value": r["id"], "label": r["name"]}
                for r in domains
                if any(s.data_scope_id == r["id"] for s in scopes)
            ],
            "agents": options("agent"),
            "actors": options("actor"),
        }
