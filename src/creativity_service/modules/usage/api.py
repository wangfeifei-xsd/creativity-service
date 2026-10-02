"""用量、价格、预算及导出路由；传输层不接受渠道覆盖。"""

from typing import Annotated, Any, cast
from urllib.parse import quote

from fastapi import APIRouter, Depends, Query, Request, Response

from creativity_service.core.primitives import unavailable
from creativity_service.modules.budgets.schemas import (
    BudgetCreate,
    BudgetUpdate,
    BudgetView,
    PlatformLimitCreate,
)
from creativity_service.modules.channels.api import Session
from creativity_service.modules.usage.assembly import UsageServices
from creativity_service.modules.usage.schemas import (
    ExchangeRateCreate,
    ExportView,
    PlatformExportCreate,
    PriceCreate,
    PriceVersionView,
    RecordDetail,
    RecordPage,
    RepriceInput,
    UsageFilter,
    UsageSummary,
)

router = APIRouter(tags=["用量与预算"])


def services(request: Request) -> UsageServices:
    value = getattr(request.app.state, "usage", None)
    if value is None:
        raise unavailable("用量与预算服务")
    return cast(UsageServices, value)


Services = Annotated[UsageServices, Depends(services)]
Filters = Annotated[UsageFilter, Query()]


@router.get("/usage/summary", response_model=UsageSummary)
async def summary(session: Session, service: Services, query: Filters) -> UsageSummary:
    scopes = await service.management.scopes(session)
    return await service.queries.summary(scopes[0].channel_id, scopes, query)


@router.get("/usage/records", response_model=RecordPage)
async def records(
    session: Session,
    service: Services,
    query: Annotated[UsageFilter, Depends()],
    offset: int = 0,
    limit: int = 50,
) -> RecordPage:
    return await service.management.records(session, query, offset, limit)


@router.get("/usage/records/{usage_id}", response_model=RecordDetail)
async def detail(usage_id: str, session: Session, service: Services) -> RecordDetail:
    return await service.management.detail(session, usage_id)


@router.post("/usage/records/{usage_id}/reprice", response_model=RecordDetail)
async def reprice(
    usage_id: str, body: RepriceInput, session: Session, service: Services
) -> RecordDetail:
    context = await service.management.context(session, "model:manage")
    await service.management.detail(session, usage_id)
    await service.ledger.reprice(context.scope, usage_id, body.price_version_id, body.revision)
    return await service.management.detail(session, usage_id)


@router.post("/usage/aggregates/rebuild", response_model=UsageSummary)
async def rebuild(body: UsageFilter, session: Session, service: Services) -> UsageSummary:
    context = await service.management.context(session, "budget:manage")
    scopes = await service.management.scopes(session)
    return await service.queries.rebuild(context.scope, scopes, body)


@router.get("/usage/prices", response_model=list[PriceVersionView])
async def prices(session: Session, service: Services) -> list[PriceVersionView]:
    return await service.management.prices(session)


@router.get("/models/{model_id}/price-versions", response_model=list[PriceVersionView])
async def model_prices(
    model_id: str, session: Session, service: Services
) -> list[PriceVersionView]:
    return await service.management.prices(session, model_id)


@router.post("/models/{model_id}/price-versions", response_model=PriceVersionView, status_code=201)
async def create_price(
    model_id: str, body: PriceCreate, session: Session, service: Services
) -> PriceVersionView:
    return await service.management.create_price(session, model_id, body)


@router.get("/budgets", response_model=list[BudgetView])
async def budgets(session: Session, service: Services) -> list[BudgetView]:
    return await service.management.budgets_list(session)


@router.post("/budgets", response_model=BudgetView, status_code=201)
async def create_budget(body: BudgetCreate, session: Session, service: Services) -> BudgetView:
    return await service.management.save_budget(session, body)


@router.patch("/budgets/{policy_id}", response_model=BudgetView)
async def update_budget(
    policy_id: str, body: BudgetUpdate, session: Session, service: Services
) -> BudgetView:
    return await service.management.save_budget(session, body, policy_id)


@router.get("/usage/alerts", response_model=list[dict[str, Any]])
async def alerts(session: Session, service: Services) -> list[dict[str, Any]]:
    return await service.management.alerts(session)


@router.post("/usage/exchange-rates", response_model=dict[str, Any], status_code=201)
async def exchange_rate(
    body: ExchangeRateCreate, session: Session, service: Services
) -> dict[str, Any]:
    return await service.management.exchange_rate(session, body)


@router.get("/platform/budget-limits", response_model=list[dict[str, Any]])
async def platform_limits(session: Session, service: Services) -> list[dict[str, Any]]:
    return await service.management.platform_limits(session)


@router.post("/platform/budget-limits", response_model=list[dict[str, Any]], status_code=201)
async def set_platform_limit(
    body: PlatformLimitCreate, session: Session, service: Services
) -> list[dict[str, Any]]:
    return await service.management.platform_limits(session, body)


@router.get("/usage/exports", response_model=list[ExportView])
async def exports(session: Session, service: Services) -> list[ExportView]:
    return await service.exports.list_exports(session)


@router.post("/usage/exports", response_model=ExportView, status_code=202)
async def create_export(body: UsageFilter, session: Session, service: Services) -> ExportView:
    return await service.exports.create(session, body)


@router.get("/usage/exports/{export_id}/content", response_class=Response)
async def download_export(export_id: str, session: Session, service: Services) -> Response:
    data = await service.exports.download(session, export_id)
    return Response(
        data,
        media_type="text/csv",
        headers={
            "Cache-Control": "private, no-store",
            "X-Content-Type-Options": "nosniff",
            "Content-Disposition": "attachment; filename*=UTF-8''" + quote("用量明细.csv"),
        },
    )


@router.get("/usage/options", response_model=dict[str, Any])
async def options(session: Session, service: Services) -> dict[str, Any]:
    return await service.management.options(session)


@router.post("/platform/usage/exports", response_model=ExportView, status_code=202)
async def create_platform_export(
    body: PlatformExportCreate, session: Session, service: Services
) -> ExportView:
    return await service.exports.create_platform(session, body.channel_ids, body.query)


@router.get("/platform/usage/exports", response_model=list[ExportView])
async def platform_exports(session: Session, service: Services) -> list[ExportView]:
    return await service.exports.platform_list(session)


@router.get("/platform/usage/exports/{export_id}/content", response_class=Response)
async def download_platform_export(export_id: str, session: Session, service: Services) -> Response:
    data = await service.exports.platform_download(session, export_id)
    return Response(
        data,
        media_type="text/csv",
        headers={
            "Cache-Control": "private, no-store",
            "X-Content-Type-Options": "nosniff",
            "Content-Disposition": "attachment; filename*=UTF-8''" + quote("渠道用量汇总.csv"),
        },
    )
