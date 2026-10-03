"""告警状态转换去重、账单缺失和差异不会修改平台用量事实。"""

from datetime import timedelta

import pytest

from creativity_service.core.database import transaction
from creativity_service.core.primitives import utcnow
from creativity_service.core.security.outbound import Destination, OutboundPolicy
from creativity_service.modules.integrations.alerts import Alerts, AlertSave
from creativity_service.modules.integrations.automation_schemas import WebhookCreate
from creativity_service.modules.integrations.webhooks import WebhookService
from creativity_service.modules.runs.repositories import save
from creativity_service.modules.usage.assembly import build_usage_services
from creativity_service.modules.usage.repositories import rows
from creativity_service.modules.usage.statements import StatementCreate, StatementLine, Statements
from creativity_service.workers.executor import execute_message
from tests.integration.automation.test_webhooks import Keys, Receiver
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


async def test_alert_trigger_resolve_new_cycle_and_disable(runtime_env, monkeypatch):
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
    receiver.status = 200
    hooks = WebhookService(env.runs.automation, policy, Keys(), receiver)
    endpoint = await hooks.create(
        env.context,
        WebhookCreate(
            name="告警接收器",
            url="http://127.0.0.1:4444/events",
            secret="s" * 32,
            events=("alert.triggered", "alert.resolved"),
        ),
    )
    alerts = Alerts(env.runs.automation, hooks)
    rule = await alerts.save(
        env.context,
        AlertSave(name="失败提醒", kind="run_failure", endpoint_id=endpoint["id"], threshold=1),
    )
    receipt, message = await admitted(env)
    env.adapter.failures = ["MODEL_UNAVAILABLE"] * 10
    await execute_message(env.runs, message, "failed-worker", env.runtime)
    assert (await env.runs.get_run(env.context, receipt.run_id)).state == "FAILED"
    from creativity_service.modules.integrations.automation_schemas import Toggle

    enqueue = hooks.enqueue
    paused = None

    async def pause_before_enqueue(*args, **kwargs):
        nonlocal paused
        paused = await hooks.toggle(
            env.context, endpoint["id"], Toggle(revision=endpoint["revision"], active=False)
        )
        return await enqueue(*args, **kwargs)

    monkeypatch.setattr(hooks, "enqueue", pause_before_enqueue)
    await alerts.sweep(env.context.scope.channel_id)
    async with env.engine.connect() as db:
        pending = await alerts.repo(env.context).get(db, rule["id"])
        assert len(pending["pending_events"]) == 1
    assert not receiver.calls
    monkeypatch.setattr(hooks, "enqueue", enqueue)
    await hooks.toggle(
        env.context, endpoint["id"], Toggle(revision=paused["revision"], active=True)
    )
    await alerts.sweep(env.context.scope.channel_id)
    await alerts.sweep(env.context.scope.channel_id)
    await hooks.sweep(env.context.scope.channel_id)
    assert len(receiver.calls) == 1
    # 更改观测窗口以模拟故障退出时间窗；不伪造新的运行成功事实。
    from sqlalchemy import update

    from creativity_service.modules.runs.tables import metadata

    async with transaction(
        env.engine, env.context.scope, env.runs.keys(env.context, receipt.run_id)
    ) as uow:
        await uow.connection.execute(
            update(metadata.tables["runs"])
            .where(metadata.tables["runs"].c.id == receipt.run_id)
            .values(updated_at=utcnow() - timedelta(hours=2))
        )
    await alerts.sweep(env.context.scope.channel_id)
    await hooks.sweep(env.context.scope.channel_id)
    assert len(receiver.calls) == 2
    async with transaction(
        env.engine, env.context.scope, env.runs.keys(env.context, receipt.run_id)
    ) as uow:
        await save(uow, "runs", receipt.run_id, {"error": None})
    await alerts.sweep(env.context.scope.channel_id)
    latest = (await alerts.list(env.context))[0]
    assert latest["generation"] == 2
    await alerts.save(
        env.context,
        AlertSave(
            revision=latest["revision"],
            name=rule["name"],
            kind="run_failure",
            endpoint_id=endpoint["id"],
            active=False,
        ),
        rule["id"],
    )
    await hooks.sweep(env.context.scope.channel_id)
    assert len(receiver.calls) == 2


async def test_statement_exact_amount_duplicate_priced_and_live_recheck(runtime_env):
    env = runtime_env
    from creativity_service.modules.usage.schemas import PriceCreate, PriceItem

    usage = build_usage_services(env.engine, env.services.channels)
    await usage.management.create_price(
        env.tenant.manager,
        env.model.id,
        PriceCreate(
            name="确定性测试价格",
            currency="USD",
            effective_at=utcnow() - timedelta(minutes=1),
            source="验收夹具",
            items=[
                PriceItem(dimension="input", amount="0.01", per_units=1),
                PriceItem(dimension="output", amount="0.01", per_units=1),
            ],
        ),
    )
    receipt, message = await admitted(env)
    await execute_message(env.runs, message, "billing-worker", env.runtime)
    statements = Statements(usage.management)
    async with env.engine.connect() as connection:
        records = await rows(
            connection, "usage_records", env.context.scope.channel_id, run_id=receipt.run_id
        )
    record = records[0]
    line = StatementLine(
        line_id="1",
        request_id=record["source_request_id"],
        occurred_at=record["sent_at"],
        amount=str(record["amount"] or "1.23"),
    )
    body = StatementCreate(
        name="供应商导入",
        version="1",
        connection_id=record["connection_id"],
        currency=record["currency"] or "USD",
        start_at=record["sent_at"] - timedelta(minutes=1),
        end_at=utcnow() + timedelta(minutes=1),
        lines=[line],
    )
    checked = await statements.create(env.tenant.manager, body)
    assert checked["results"][0]["state"] == "MATCHED"
    assert checked["results"][0]["platform_amount"] == "0.30000000"
    replay = await statements.create(env.tenant.manager, body)
    assert replay["id"] == checked["id"]
    duplicated = await statements.create(
        env.tenant.manager, body.model_copy(update={"version": "2", "lines": [line, line]})
    )
    assert [r["state"] for r in duplicated["results"]] == ["DUPLICATE", "DUPLICATE"]
    missing = await statements.create(
        env.tenant.manager,
        body.model_copy(
            update={"version": "3", "lines": [line.model_copy(update={"request_id": "missing"})]}
        ),
    )
    assert {r["state"] for r in missing["results"]} == {"PLATFORM_MISSING", "PROVIDER_MISSING"}
    async with env.engine.connect() as connection:
        after = await rows(
            connection, "usage_records", env.context.scope.channel_id, run_id=receipt.run_id
        )
    assert after == records

    from creativity_service.modules.usage.repositories import ledger_key
    from creativity_service.modules.usage.repositories import save as save_usage

    changed = await statements.create(
        env.tenant.manager,
        body.model_copy(
            update={"version": "4", "lines": [line.model_copy(update={"amount": line.amount + 1})]}
        ),
    )
    assert changed["results"][0]["state"] == "DIFFERENCE"
    assert changed["results"][0]["difference"] == "1.00000000"
    foreign = await statements.create(
        env.tenant.manager, body.model_copy(update={"version": "5", "currency": "CNY"})
    )
    assert foreign["results"][0]["state"] == "CURRENCY_MISMATCH"
    assert foreign["results"][0]["difference"] is None
    # 模拟账本收到最终迟报后的记录；核查再次读取当前事实，不覆写原账单。
    async with transaction(
        env.engine, env.context.scope, [ledger_key(env.context.scope.channel_id)]
    ) as uow:
        await save_usage(uow, "usage_records", record["id"], {"final_reported": True})
    rechecked = await statements.detail(env.tenant.manager, checked["id"])
    assert rechecked["results"][0]["late_reported"] is True
