"""在真实独立进程终止后检查已提交预占，不把未知消耗当成零。"""

import asyncio
import json
import os
import sys
from datetime import timedelta

import pytest
from sqlalchemy import update

from creativity_service.core.primitives import utcnow
from creativity_service.modules.usage.tables import metadata
from tests.integration.usage.test_ledger import budget, prepare, price, stored

pytestmark = pytest.mark.integration


async def test_process_exit_after_send_intent_keeps_durable_pending_reservation(usage_env):
    env = usage_env
    await price(env)
    await budget(env)
    plan = await prepare(env, send=False)
    program = """
import asyncio, json, os
from sqlalchemy.ext.asyncio import create_async_engine
from creativity_service.core.context import Scope
from creativity_service.modules.budgets.services import BudgetService
from creativity_service.modules.usage.services import UsageService
async def run():
    engine = create_async_engine(
        os.environ["USAGE_TEST_DSN"],
        isolation_level="READ COMMITTED",
    )
    scope = Scope.model_validate(json.loads(os.environ["USAGE_TEST_SCOPE"]))
    ledger = UsageService(engine, BudgetService(engine))
    await ledger.mark_sent(scope, os.environ["USAGE_TEST_ATTEMPT"])
    os._exit(9)
asyncio.run(run())
"""
    process = await asyncio.create_subprocess_exec(
        sys.executable,
        "-c",
        program,
        env={
            **os.environ,
            "USAGE_TEST_DSN": env.engine.url.render_as_string(hide_password=False),
            "USAGE_TEST_SCHEMA": env.schema,
            "USAGE_TEST_SCOPE": json.dumps(env.scope.model_dump()),
            "USAGE_TEST_ATTEMPT": plan.attempt_id,
        },
    )
    assert await asyncio.wait_for(process.wait(), timeout=20) == 9
    reservations = metadata.tables["budget_reservations"]
    async with env.engine.begin() as connection:
        await connection.execute(
            update(reservations)
            .where(reservations.c.channel_id == env.scope.channel_id)
            .values(expires_at=utcnow() - timedelta(minutes=1))
        )
    for _ in range(2):
        assert await env.usage.ledger.compensate(env.scope) == {"released": 0, "pending": 1}
    record = (await stored(env, "usage_records"))[0]
    reservation = (await stored(env, "budget_reservations"))[0]
    assert record["state"] == "PENDING" and record["amount"] is None
    assert reservation["reserved_amount"] == 2 and reservation["settled_amount"] is None
