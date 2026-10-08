"""API 与 Worker 共用调度及投递装配，未登记的出站地址保持拒绝。"""

from typing import Any

from pydantic_settings import SettingsConfigDict

from creativity_service.core.security.outbound import Destination, OutboundPolicy
from creativity_service.modules.iam.authorization import IamAuthorization
from creativity_service.modules.integrations.alerts import Alerts
from creativity_service.modules.integrations.assembly import ConfiguredKeys, IntegrationSettings
from creativity_service.modules.integrations.automation import AutomationService
from creativity_service.modules.integrations.webhooks import WebhookService
from creativity_service.modules.runs.services import RunService


class AutomationSettings(IntegrationSettings):
    model_config = SettingsConfigDict(
        env_prefix="CREATIVITY_AUTOMATION_",
        env_file=".env",
        extra="ignore",
        hide_input_in_errors=True,
    )

    destinations: list[dict[str, Any]] = []


def install_automation(runs: RunService, authorization: IamAuthorization) -> None:
    settings = AutomationSettings()
    service = AutomationService(runs, authorization)
    policy = OutboundPolicy(tuple(Destination(**value) for value in settings.destinations))
    provider = (
        ConfiguredKeys(settings.key_version, settings.encryption_keys)
        if settings.key_version
        else None
    )
    webhooks = WebhookService(service, policy, provider)
    runs.automation, runs.webhooks = service, webhooks
    alerts = Alerts(service, webhooks)

    async def sweep(channel_id: str) -> None:
        await service.sweep(channel_id)
        await alerts.sweep(channel_id)
        await webhooks.sweep(channel_id)

    runs.integration_sweep = sweep
