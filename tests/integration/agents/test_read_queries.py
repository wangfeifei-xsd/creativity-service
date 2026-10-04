"""真实数据库验证列表扩容不增加逐项查询，下一请求仍检查会话撤销。"""

from collections import Counter
from contextlib import contextmanager

import pytest
from sqlalchemy import event

from creativity_service.core.primitives import ServiceError

pytestmark = pytest.mark.integration


@contextmanager
def statements(engine):
    queries = []

    def record(connection, cursor, statement, parameters, context, many):
        queries.append(statement)

    event.listen(engine.sync_engine, "before_cursor_execute", record)
    try:
        yield queries
    finally:
        event.remove(engine.sync_engine, "before_cursor_execute", record)


async def test_agent_list_has_constant_query_count_and_revalidates_next_request(agent_env):
    env = agent_env
    await env.agents.create(env.context, env.body)
    with statements(env.engine) as single:
        one = await env.agents.list_agents(env.context)
    for index in range(1, 10):
        await env.agents.create(
            env.context, env.body.model_copy(update={"agent_code": f"query_agent_{index}"})
        )
    with statements(env.engine) as multiple:
        ten = await env.agents.list_agents(env.context)
    assert len(one.items) == 1 and len(ten.items) == 10
    assert len(single) == len(multiple) <= 12
    assert all(item.actions == [] for item in ten.items)
    counts = Counter(
        table
        for statement in multiple
        for table in ("platform_accounts", "custom_roles", "resource_grants")
        if f"FROM {table}" in statement
    )
    assert counts == {"platform_accounts": 1, "custom_roles": 1, "resource_grants": 1}
    await env.iam.sessions.logout(env.tenant.manager)
    with pytest.raises(ServiceError) as denied:
        await env.agents.list_agents(env.context)
    assert denied.value.status == 401
