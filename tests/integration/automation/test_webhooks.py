"""真实运行终态形成稳定事件，签名、重投与停用不泄露运行正文。"""

import asyncio
import hashlib
import hmac
import json
from datetime import timedelta

import pytest
from pydantic import SecretBytes

from creativity_service.core.database import assert_external_io_allowed
from creativity_service.core.security.outbound import Destination, OutboundPolicy
from creativity_service.integrations.outbound import HttpResponse
from creativity_service.modules.integrations import webhooks
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


async def configured(env, *, client_ids=(), events=("run.terminal",)):
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
        WebhookCreate(
            name="本地验收接收器",
            url="http://127.0.0.1:4444/events",
            secret="s" * 32,
            client_ids=list(client_ids),
            events=events,
        ),
    )
    return service, endpoint, receiver


async def test_webhook_stable_event_signature_manual_retry_and_disabled(runtime_env):
    env = runtime_env
    service, endpoint, receiver = await configured(env)
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


async def test_webhook_retry_budget_survives_worker_crashes(runtime_env, monkeypatch):
    env = runtime_env
    service, _, receiver = await configured(env)
    _, message = await admitted(env)
    await execute_message(env.runs, message, "webhook-crash-worker", env.runtime)
    instant = webhooks.utcnow()
    monkeypatch.setattr(webhooks, "utcnow", lambda: instant)
    post = receiver.post

    async def interrupted(*args, **kwargs):
        await post(*args, **kwargs)
        # 请求已发出但 Worker 未持久化结果，恢复只能消耗同一轮剩余次数。
        raise asyncio.CancelledError()

    monkeypatch.setattr(receiver, "post", interrupted)
    for _ in range(6):
        with pytest.raises(asyncio.CancelledError):
            await service.sweep(env.context.scope.channel_id)
        instant += timedelta(seconds=61)
    await service.sweep(env.context.scope.channel_id)
    row = (await service.deliveries(env.context))[0]
    assert row["state"] == "FAILED" and row["attempts"] == 6
    assert len(receiver.calls) == 6
    event = json.loads(receiver.calls[0][0])
    assert all(json.loads(body) == event for body, _ in receiver.calls)
    monkeypatch.setattr(receiver, "post", post)
    receiver.status = 200
    await service.retry(env.context, row["id"], row["revision"])
    await service.sweep(env.context.scope.channel_id)
    row = (await service.deliveries(env.context))[0]
    assert row["state"] == "SUCCEEDED" and row["attempts"] == 7
    assert json.loads(receiver.calls[-1][0]) == event


async def test_terminal_scan_batches_new_runs_and_skips_delivery_history(runtime_env):
    from sqlalchemy import insert, select

    from creativity_service.modules.integrations.automation_tables import metadata as automation
    from creativity_service.modules.runs.tables import metadata
    from tests.integration.agents.test_read_queries import statements

    env = runtime_env
    service, endpoint, _ = await configured(env)
    _, message = await admitted(env)
    await execute_message(env.runs, message, "webhook-batch", env.runtime)
    row = await env.runs.load(message)
    target = await service.automation.get(env.context, "webhook_endpoints", endpoint["id"])
    with statements(env.engine) as single:
        await service.terminal_events(target)
    async with env.engine.begin() as connection:
        await connection.execute(
            insert(metadata.tables["runs"]),
            [{**row, "id": f"webhook_batch_{i:03}"} for i in range(20)],
        )
    with statements(env.engine) as multiple:
        await service.terminal_events(target)

    # 除互斥锁外，读库次数固定，不逐运行重读身份、服务或删除状态。
    def reads(queries):
        return [q for q in queries if q.lstrip().startswith("SELECT") and "pg_advisory" not in q]

    assert len(reads(multiple)) <= len(reads(single)) + 2
    async with env.engine.begin() as connection:
        await connection.execute(
            insert(metadata.tables["runs"]),
            [{**row, "id": f"webhook_batch_{i:03}"} for i in range(20, 225)],
        )
    await service.terminal_events(target)
    with statements(env.engine) as replay:
        await service.terminal_events(target)
    assert not any("FROM service_clients" in q for q in replay)
    assert sum("FROM runs" in q for q in replay) == 1
    async with env.engine.connect() as connection:
        deliveries = [
            dict(r)
            for r in (
                await connection.execute(select(automation.tables["webhook_deliveries"]))
            ).mappings()
        ]
    assert len(deliveries) == 226
    assert len({r["event_id"] for r in deliveries}) == 226
    assert {r["payload"]["run_id"] for r in deliveries} == {
        row["id"],
        *[f"webhook_batch_{i:03}" for i in range(225)],
    }
