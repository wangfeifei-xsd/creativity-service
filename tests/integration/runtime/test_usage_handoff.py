"""模型发送证据、迟报和调用终态在运行与账本之间一致交接。"""

import pytest

from creativity_service.integrations.models.contracts import ModelEvent
from creativity_service.integrations.models.usage import normalize_usage
from creativity_service.modules.usage.query import totals
from creativity_service.modules.usage.repositories import rows
from creativity_service.workers.executor import execute_message
from tests.integration.runtime.test_execution import admitted

pytestmark = pytest.mark.integration


@pytest.mark.parametrize("request_sent", [False, True, None])
@pytest.mark.parametrize("terminal", ["failed", "cancelled"])
async def test_only_confirmed_unsent_releases_budget(runtime_env, request_sent, terminal):
    env = runtime_env
    receipt, message = await admitted(env)

    async def events(context, config, request, attempt, reservation, cancellation, **kwargs):
        yield ModelEvent(
            kind="usage",
            request_sent=request_sent,
            attempt_id=attempt.attempt_id,
            usage=normalize_usage(
                config, attempt.attempt_id, attempt.attempt_id, None, final=False
            ),
        )
        yield ModelEvent(
            kind=terminal,
            request_sent=request_sent,
            attempt_id=attempt.attempt_id,
            error_code="CREDENTIAL_UNAVAILABLE",
            message="模拟发送边界失败",
        )

    env.adapter.events = events
    await execute_message(env.runs, message, "handoff", env.runtime)
    async with env.engine.connect() as connection:
        (record,) = await rows(
            connection, "usage_records", env.context.scope.channel_id, run_id=receipt.run_id
        )
        reservations = await rows(
            connection,
            "budget_reservations",
            env.context.scope.channel_id,
            attempt_id=record["attempt_id"],
        )
    expected = "RELEASED" if request_sent is False else "PENDING"
    assert record["state"] == expected
    assert record["outcome"] == "FAILED"
    assert all(r["status"] == expected for r in reservations)
    assert await env.runs.ledger.release_unused(env.context.scope, record["attempt_id"]) == expected


async def test_successful_run_persists_usage_outcome(runtime_env):
    env = runtime_env
    receipt, message = await admitted(env)
    await execute_message(env.runs, message, "handoff", env.runtime)
    async with env.engine.connect() as connection:
        records = await rows(
            connection, "usage_records", env.context.scope.channel_id, run_id=receipt.run_id
        )
    assert (await env.runs.load(message))["state"] == "SUCCEEDED"
    assert records[0]["state"] == "SETTLED"
    assert records[0]["outcome"] == "SUCCEEDED"
    assert totals(records, [], "UTC").success_rate == "1.0000"


async def test_compensation_repairs_historical_terminal_outcomes(runtime_env):
    from sqlalchemy import update

    from creativity_service.modules.usage.tables import metadata

    env = runtime_env
    receipt, message = await admitted(env)
    await execute_message(env.runs, message, "historical-outcome", env.runtime)
    table = metadata.tables["usage_records"]
    async with env.engine.begin() as connection:
        await connection.execute(
            update(table).where(table.c.run_id == receipt.run_id).values(outcome="PENDING")
        )
    assert await env.runs.ledger.reconcile_outcomes(env.context.scope, limit=1) == 1
    assert await env.runs.ledger.reconcile_outcomes(env.context.scope, limit=1) == 0
    async with env.engine.connect() as connection:
        (record,) = await rows(
            connection, "usage_records", env.context.scope.channel_id, run_id=receipt.run_id
        )
    assert record["outcome"] == "SUCCEEDED" and record["state"] == "SETTLED"


async def test_success_without_final_usage_keeps_accounting_pending(runtime_env):
    env = runtime_env
    receipt, message = await admitted(env)
    original = env.adapter.events

    async def missing_usage(context, config, request, attempt, reservation, cancellation, **kwargs):
        async for item in original(
            context, config, request, attempt, reservation, cancellation, **kwargs
        ):
            if item.kind == "usage":
                item = item.model_copy(
                    update={
                        "usage": normalize_usage(
                            config, attempt.attempt_id, attempt.attempt_id, None, final=False
                        ),
                        "request_sent": True,
                    }
                )
            yield item

    env.adapter.events = missing_usage
    await execute_message(env.runs, message, "missing-usage", env.runtime)
    async with env.engine.connect() as connection:
        (record,) = await rows(
            connection, "usage_records", env.context.scope.channel_id, run_id=receipt.run_id
        )
    assert (await env.runs.load(message))["state"] == "SUCCEEDED"
    assert record["outcome"] == "SUCCEEDED" and record["state"] == "PENDING"
