"""平台限额当前版本、历史占用聚合与管理工作区边界。"""

from datetime import timedelta

import pytest
from sqlalchemy import insert

from creativity_service.core.primitives import ServiceError, utcnow
from creativity_service.modules.budgets.platform import current_limits, occupancy_counts
from creativity_service.modules.budgets.schemas import PlatformLimitCreate
from creativity_service.modules.budgets.services import period_start
from creativity_service.modules.usage.repositories import rows
from creativity_service.modules.usage.tables import metadata
from tests.integration.agents.test_read_queries import statements
from tests.integration.usage.test_ledger import admit, plan

pytestmark = pytest.mark.integration


async def test_current_platform_limit_revision_occupancy_and_scope(usage_env):
    env = usage_env
    management = env.usage.management
    body = PlatformLimitCreate(
        limit_code="concurrency", name="平台并发", unit="concurrency", limit_value=20
    )
    limits = await management.platform_limits(env.admin, body)
    assert len(limits) == 1 and limits[0].revision == 1
    assert (limits[0].used, limits[0].remaining) == (0, 20)
    p = plan()
    await admit(env, p)
    limits = await management.platform_limits(
        env.admin, body.model_copy(update={"revision": 1, "limit_value": 30})
    )
    assert len(limits) == 1 and limits[0].revision == 2
    assert (limits[0].used, limits[0].remaining) == (1, 29)
    with pytest.raises(ServiceError) as conflict:
        await management.platform_limits(env.admin, body.model_copy(update={"revision": 1}))
    assert conflict.value.code == "REVISION_CONFLICT"
    with pytest.raises(ServiceError) as forbidden:
        await management.platform_limits(env.channel.manager)
    assert forbidden.value.code == "FORBIDDEN"
    limits = await management.platform_limits(
        env.admin, body.model_copy(update={"revision": 2, "status": "DISABLED"})
    )
    assert len(limits) == 1 and limits[0].revision == 3
    assert limits[0].used is None and limits[0].remaining is None
    await env.usage.budgets.finish_admission(env.context, p.run_id)


async def test_released_history_is_aggregated_with_correct_concurrency_and_period(usage_env):
    env = usage_env
    management = env.usage.management
    for code, unit in (("active", "concurrency"), ("daily", "requests")):
        await management.platform_limits(
            env.admin,
            PlatformLimitCreate(
                limit_code=code, name=code, unit=unit, limit_value=20, period="day"
            ),
        )
    p = plan()
    await admit(env, p)
    now = utcnow()
    async with env.engine.begin() as connection:
        initial = await rows(connection, "platform_quota_occupancies", "system", run_id=p.run_id)
        assert len(initial) == 2
        history = [
            {
                **row,
                "id": f"history_{row['limit_code']}_{index}",
                "run_id": f"old_{index}",
                "status": "RELEASED",
                "created_at": now - timedelta(days=30),
                "updated_at": now - timedelta(days=30),
            }
            for row in initial
            for index in range(1000)
        ]
        await connection.execute(insert(metadata.tables["platform_quota_occupancies"]), history)
    # 当期已完成请求计入请求量，不能继续占用并发；更早周期和其他代码不进入返回行。
    await env.usage.budgets.finish_admission(env.context, p.run_id)
    with statements(env.engine) as queries:
        async with env.engine.connect() as connection:
            limits = await current_limits(connection, now)
            counts = await occupancy_counts(
                connection,
                limits,
                {
                    row["limit_code"]: period_start(now, row["period"], row["timezone"])
                    for row in limits
                },
            )
    assert counts == {"daily": 1}
    assert len(queries) == 2
    assert "count(" in queries[-1].lower() and "GROUP BY" in queries[-1]
    await admit(env, plan())
