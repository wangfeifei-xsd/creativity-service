"""调度与投递配置面和统一批次受理接口。"""

from typing import Annotated, Any

from fastapi import APIRouter, Depends, Header, Request

from creativity_service.core.context import AuthContext, require_http_context
from creativity_service.core.primitives import Contract, Revision, unavailable
from creativity_service.modules.integrations.alerts import Alerts, AlertSave
from creativity_service.modules.integrations.automation import AutomationService
from creativity_service.modules.integrations.automation_schemas import (
    BatchCancel,
    BatchCreate,
    BatchView,
    ItemView,
    ScheduleCreate,
    Toggle,
    WebhookCreate,
    WebhookUpdate,
)
from creativity_service.modules.integrations.webhooks import WebhookService

router = APIRouter(tags=["定时与事件集成"])
batch_router = APIRouter(tags=["批量运行"])
Context = Annotated[AuthContext, Depends(require_http_context)]


def automation(request: Request) -> AutomationService:
    service: AutomationService | None = getattr(request.app.state.runs, "automation", None)
    if service is None:
        raise unavailable("自动化服务")
    return service


def webhook(request: Request) -> WebhookService:
    service: WebhookService | None = getattr(request.app.state.runs, "webhooks", None)
    if service is None:
        raise unavailable("事件投递服务")
    return service


Automation = Annotated[AutomationService, Depends(automation)]
Webhooks = Annotated[WebhookService, Depends(webhook)]


class RevisionInput(Contract):
    revision: Revision


@router.get("/schedules")
async def schedules(context: Context, service: Automation) -> list[dict[str, Any]]:
    return await service.list_schedules(context)


@router.post("/schedules", status_code=201)
async def create_schedule(
    context: Context, service: Automation, body: ScheduleCreate
) -> dict[str, Any]:
    return await service.create_schedule(context, body)


@router.patch("/schedules/{identifier}")
async def toggle_schedule(
    context: Context, service: Automation, identifier: str, body: Toggle
) -> dict[str, Any]:
    return await service.toggle_schedule(context, identifier, body)


@batch_router.post("/batches", status_code=202)
async def create_batch(
    context: Context,
    service: Automation,
    body: BatchCreate,
    idempotency_key: Annotated[str, Header()],
) -> BatchView:
    return await service.create_batch(context, body, idempotency_key)


@batch_router.get("/batches/{identifier}")
async def batch(context: Context, service: Automation, identifier: str) -> BatchView:
    return await service.batch(context, identifier)


@batch_router.post("/batches/{identifier}/cancel")
async def cancel_batch(
    context: Context, service: Automation, identifier: str, body: BatchCancel
) -> BatchView:
    return await service.cancel_batch(context, identifier, body.revision, body.cancel_runs)


@batch_router.post("/batch-items/{identifier}/retry")
async def retry_item(
    context: Context, service: Automation, identifier: str, body: RevisionInput
) -> ItemView:
    return await service.retry_item(context, identifier, body.revision)


@router.get("/webhooks")
async def endpoints(context: Context, service: Webhooks) -> list[dict[str, Any]]:
    return await service.endpoints(context)


@router.post("/webhooks", status_code=201)
async def create_endpoint(
    context: Context, service: Webhooks, body: WebhookCreate
) -> dict[str, Any]:
    return await service.create(context, body)


@router.patch("/webhooks/{identifier}")
async def toggle_endpoint(
    context: Context, service: Webhooks, identifier: str, body: WebhookUpdate
) -> dict[str, Any]:
    return await service.toggle(context, identifier, body)


@router.get("/run-subscription-options")
async def subscription_options(context: Context, service: Webhooks) -> list[dict[str, Any]]:
    return await service.subscriptions.options(context)


@router.get("/webhook-deliveries")
async def deliveries(context: Context, service: Webhooks) -> list[dict[str, Any]]:
    return await service.deliveries(context)


@router.post("/webhook-deliveries/{identifier}/retry")
async def retry_delivery(
    context: Context, service: Webhooks, identifier: str, body: RevisionInput
) -> dict[str, bool]:
    await service.retry(context, identifier, body.revision)
    return {"accepted": True}


@router.get("/batches")
async def batches(context: Context, service: Automation) -> list[BatchView]:
    return await service.list_batches(context)


@router.get("/alert-rules")
async def alert_rules(
    context: Context, service: Automation, webhooks: Webhooks
) -> list[dict[str, Any]]:
    return await Alerts(service, webhooks).list(context)


@router.post("/alert-rules", status_code=201)
async def create_alert(
    context: Context, service: Automation, webhooks: Webhooks, body: AlertSave
) -> dict[str, Any]:
    return await Alerts(service, webhooks).save(context, body)


@router.patch("/alert-rules/{identifier}")
async def edit_alert(
    context: Context, service: Automation, webhooks: Webhooks, body: AlertSave, identifier: str
) -> dict[str, Any]:
    return await Alerts(service, webhooks).save(context, body, identifier)
