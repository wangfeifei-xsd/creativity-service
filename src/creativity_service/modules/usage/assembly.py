"""用量模块生产装配及模型价格只读适配端口。"""

from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncEngine

from creativity_service.core.artifacts import ObjectStore
from creativity_service.core.context import AuthContext
from creativity_service.core.database import UnitOfWork
from creativity_service.core.primitives import ServiceError, utcnow
from creativity_service.modules.budgets.services import BudgetService, active_price
from creativity_service.modules.channels.services import ChannelService
from creativity_service.modules.models.schemas import FrozenModel
from creativity_service.modules.models.schemas import PriceView as ModelPriceView
from creativity_service.modules.usage.exports import UsageExports
from creativity_service.modules.usage.management import UsageManagement
from creativity_service.modules.usage.pricing import calculate
from creativity_service.modules.usage.query import UsageQueries
from creativity_service.modules.usage.repositories import rows
from creativity_service.modules.usage.services import UsageService


class ModelPrices:
    def __init__(self, management: UsageManagement) -> None:
        self.management = management

    async def read(self, context: AuthContext, model_id: str) -> ModelPriceView:
        async with self.management.engine.connect() as connection:
            await self.management.model(connection, context.scope.channel_id, model_id)
            choices = [
                p
                for p in await rows(
                    connection, "price_versions", context.scope.channel_id, model_id=model_id
                )
                if p["effective_at"] <= utcnow()
            ]
        row = max(choices, key=lambda p: (p["effective_at"], p["created_at"])) if choices else None
        return ModelPriceView(
            model_id=model_id,
            available=row is not None,
            source=row["source"] if row else None,
            currency=row["currency"] if row else None,
            price_items=row["price_items"] if row else [],
            reason=None if row else "尚未配置有效价格",
        )

    async def require_priced(
        self, uow: UnitOfWork, context: AuthContext, models: list[FrozenModel]
    ) -> None:
        uow.require_scope(context.scope)
        for model in models:
            if model.scope.channel_id != context.scope.channel_id:
                raise ServiceError("SCOPE_MISMATCH", "候选模型不属于当前渠道", 403)
            price = await active_price(uow, model.model_id, utcnow())
            limits = [
                model.parameters.get(k)
                for k in ("max_tokens", "max_output_tokens", "max_completion_tokens")
            ]
            output_limit = max((v for v in limits if type(v) is int and v > 0), default=0)
            amount = None
            if price is not None and output_limit:
                tokens = {
                    "input": 1,
                    "output": output_limit,
                    **{child: 0 for child in price["subset_relations"]},
                }
                amount, _, _ = calculate(
                    price, tokens, price["subset_relations"], "ESTIMATED", upper=True
                )
            if amount is None:
                raise ServiceError(
                    "BUDGET_PRICE_REQUIRED", "金额硬预算要求候选模型具备完整价格及输出上限", 422
                )


@dataclass(frozen=True)
class UsageServices:
    budgets: BudgetService
    ledger: UsageService
    management: UsageManagement
    queries: UsageQueries
    exports: UsageExports
    prices: ModelPrices


def build_usage_services(
    engine: AsyncEngine, channels: ChannelService, store: ObjectStore | None = None
) -> UsageServices:
    budgets = BudgetService(engine)
    ledger, queries = UsageService(engine, budgets), UsageQueries(engine)
    management = UsageManagement(engine, channels, budgets, ledger, queries)
    services = UsageServices(
        budgets,
        ledger,
        management,
        queries,
        UsageExports(management, store),
        ModelPrices(management),
    )
    channels.usage_reader = queries
    return services
