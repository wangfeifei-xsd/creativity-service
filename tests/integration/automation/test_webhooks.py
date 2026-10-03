"""真实运行终态形成稳定事件，签名、重投与停用不泄露运行正文。"""

import hashlib
import hmac
import json

import pytest
from pydantic import SecretBytes

from creativity_service.core.database import assert_external_io_allowed
from creativity_service.core.security.outbound import Destination, OutboundPolicy
from creativity_service.integrations.outbound import HttpResponse
from creativity_service.modules.integrations.automation_schemas import Toggle, WebhookCreate
from creativity_service.modules.integrations.webhooks import WebhookService
from creativity_service.workers.executor import execute_message
from tests.integration.runtime.test_execution import admitted

pytestmark = [
    pytest.mark.integration,
    pytest.mark.parametrize(
        "agent_env",
        [
            {
                "environment": "test",
                "independent_actions": ["release:publish", "data:read_sensitive", "data:export"],
            }
        ],
        indirect=True,
    ),
]


class Keys:
    async def current(self):
        return "v1", SecretBytes(b"1" * 32)

    async def resolve(self, version):
        return SecretBytes(b"1" * 32)


class Receiver:
    def __init__(self):
        self.calls = []
        self.status = 400

    async def post(self, scope, purpose, url, body, headers):
        assert_external_io_allowed()
        self.calls.append((body, headers))
        signature = hmac.new(
            b"s" * 32, headers["X-Creativity-Timestamp"].encode() + b"." + body, hashlib.sha256
        ).hexdigest()
        assert headers["X-Creativity-Signature"] == "v1=" + signature
        return HttpResponse(self.status, b"")


async def test_webhook_stable_event_signature_manual_retry_and_disabled(runtime_env):
    env = runtime_env
    policy = OutboundPolicy(
        (
            Destination(
                env.context.scope.channel_id,
                "test",
                "webhook",
                "127.0.0.1",
                port=4444,
                scheme="http",
                allowed_networks=("127.0.0.1/32",),
            ),
        )
    )
    receiver = Receiver()
    service = WebhookService(env.runs.automation, policy, Keys(), receiver)
    endpoint = await service.create(
        env.context,
        WebhookCreate(name="本地验收接收器", url="http://127.0.0.1:4444/events", secret="s" * 32),
    )
    receipt, message = await admitted(env)
    await execute_message(env.runs, message, "webhook-worker", env.runtime)
    await service.sweep(env.context.scope.channel_id)
    rows = await service.deliveries(env.context)
    assert len(rows) == 1 and rows[0]["state"] == "FAILED"
    event = json.loads(receiver.calls[0][0])
    assert set(event) == {"event_id", "type", "run_id", "state", "status_path", "occurred_at"}
    assert event["run_id"] == receipt.run_id
    receiver.status = 200
    await service.retry(env.context, rows[0]["id"], rows[0]["revision"])
    await service.sweep(env.context.scope.channel_id)
    assert json.loads(receiver.calls[1][0]) == event
    await service.sweep(env.context.scope.channel_id)
    assert len(receiver.calls) == 2
    assert (await service.deliveries(env.context))[0]["attempts"] == 2
    await service.toggle(
        env.context, endpoint["id"], Toggle(revision=endpoint["revision"], active=False)
    )
    assert (await service.endpoints(env.context))[0]["state"] == "PAUSED"
    assert "secret" not in json.dumps(await service.endpoints(env.context))
