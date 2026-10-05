"""建议并发配额和真实同步窗口；外部并发不与受理占用混淆。"""

import asyncio
import json
import os
from datetime import timedelta
from pathlib import Path
from time import perf_counter

import pytest
from fastapi import Request, Response

from creativity_service.core.database import transaction
from creativity_service.core.primitives import RunInput, ServiceError, utcnow
from creativity_service.modules.budgets.schemas import BudgetCreate, PlatformLimitCreate
from creativity_service.modules.runs.api import admit as http_admit
from creativity_service.modules.runs.repositories import save
from creativity_service.modules.usage.repositories import rows
from creativity_service.workers.executor import execute_message
from tests.integration.channels.conftest import provision
from tests.integration.runtime.test_execution import admitted
from tests.integration.usage.test_ledger import admit, plan

pytestmark = pytest.mark.integration


def record(name, value):
    if folder := os.environ.get("CREATIVITY_ACCEPTANCE_DIR"):
        path = Path(folder) / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n")


async def test_channel_five_platform_twenty_and_fair_admission(usage_env):
    env = usage_env
    tenants = [env.channel]
    for index in range(1, 5):
        tenants.append(await provision(env, f"{index}容量测试", None))
    for tenant in tenants:
        await env.usage.management.save_budget(
            tenant.manager,
            BudgetCreate(
                name="渠道并发五",
                scope_type="channel",
                scope_id=tenant.channel.channel_id,
                unit="concurrency",
                limit_value="5",
            ),
        )
    await env.usage.management.platform_limits(
        env.admin,
        PlatformLimitCreate(
            limit_code="acceptance_parallel",
            name="平台并发二十",
            unit="concurrency",
            limit_value=20,
        ),
    )
    # 第一渠道的二十个竞争者只能占用五份；其余渠道仍能受理十五份。
    results = await asyncio.gather(
        *(admit(env, plan(), tenants[0].manager.context) for _ in range(20)),
        return_exceptions=True,
    )
    accepted = [value for value in results if not isinstance(value, Exception)]
    refused = [value for value in results if isinstance(value, ServiceError)]
    assert len(accepted) == 5 and len(refused) == 15
    assert {value.code for value in refused} == {"BUDGET_EXCEEDED"}
    for tenant in tenants[1:4]:
        accepted.extend(
            await asyncio.gather(*(admit(env, plan(), tenant.manager.context) for _ in range(5)))
        )
    with pytest.raises(ServiceError) as failure:
        await admit(env, plan(), tenants[4].manager.context)
    assert failure.value.code == "PLATFORM_LIMIT_EXCEEDED"
    async with env.engine.connect() as connection:
        held = await rows(connection, "platform_quota_occupancies", "system", status="HELD")
    assert len(held) == 20
    await env.usage.budgets.finish_admission(tenants[0].manager.context, accepted[0].run_id)
    replacement = await admit(env, plan(), tenants[4].manager.context)
    assert replacement.run_id != accepted[0].run_id
    record(
        "capacity.json",
        {
            "channel_limit": 5,
            "platform_limit": 20,
            "first_channel_accepted": 5,
            "first_channel_refused": 15,
            "other_channels_accepted": 15,
            "platform_held": len(held),
            "replacement_after_release": True,
            "boundary": "真实预算受理事务；没有启动外部模型请求，不代表外部并发二十",
        },
    )


@pytest.mark.parametrize(
    "agent_env",
    [{"environment": "test", "independent_actions": ["release:publish", "data:read_sensitive"]}],
    indirect=True,
)
async def test_real_fifteen_second_sync_window_retains_run(runtime_env):
    env = runtime_env
    receipt, _ = await admitted(env, purpose="production")
    app = env.client._transport.app
    app.state.sync_wait_seconds = 15
    started = perf_counter()
    response = Response(status_code=202)
    repeated = await http_admit(
        RunInput(agent_code=env.body.agent_code, input={"request": "请处理"}, delivery="sync"),
        "production",
        env.context,
        env.runs,
        Request({"type": "http", "app": app}),
        response,
    )
    elapsed = perf_counter() - started
    assert 15 <= elapsed < 30
    assert response.status_code == 202 and repeated.run_id == receipt.run_id
    row = await env.runs.get_run(env.context, receipt.run_id)
    assert row.state == "QUEUED"
    assert not env.adapter.calls
    await env.runs.cancel(env.context, receipt.run_id)
    record("sync-window.json", {"seconds": elapsed, "status": 202, "same_run": True})


@pytest.mark.parametrize("seconds", [60, 300])
async def test_queued_deadline_uses_configured_sixty_or_three_hundred(runtime_env, seconds):
    env = runtime_env
    definition = env.definition.model_copy(
        update={"limits": env.definition.limits.model_copy(update={"deadline_seconds": seconds})}
    )
    receipt, message = await admitted(env, definition=definition)
    row = await env.runs.load(message)
    assert row["timeout_seconds"] == seconds
    assert seconds - 1 < (row["deadline"] - row["created_at"]).total_seconds() <= seconds
    # 故障注入仅推进持久化截止时间，验证排队已耗尽预算时 Worker 不调用模型。
    async with transaction(
        env.engine, env.context.scope, env.runs.keys(env.context, receipt.run_id)
    ) as uow:
        await save(uow, "runs", receipt.run_id, {"deadline": utcnow() - timedelta(seconds=1)})
    await execute_message(env.runs, message, "expired-worker", env.runtime)
    assert (await env.runs.load(message))["state"] == "TIMED_OUT"
    assert not env.adapter.calls
