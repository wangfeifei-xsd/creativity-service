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
        for table in ("platform_accounts", "channel_memberships", "custom_roles", "resource_grants")
        if f"FROM {table}" in statement
    )
    assert counts == {
        "platform_accounts": 1,
        "channel_memberships": 1,
        # 平台账号与渠道成员分别读取各自的角色目录，次数不随列表条目增长。
        "custom_roles": 2,
        "resource_grants": 1,
    }
    await env.iam.sessions.logout(env.tenant.manager)
    with pytest.raises(ServiceError) as denied:
        await env.agents.list_agents(env.context)
    assert denied.value.status == 401


@pytest.mark.parametrize(
    "agent_env",
    [{"environment": "test", "independent_actions": ["release:publish", "data:read_sensitive"]}],
    indirect=True,
)
async def test_memory_list_batches_distinct_subjects_without_leaking_them(agent_env):
    from creativity_service.core.deletion import RecoveryService
    from creativity_service.modules.memory.assembly import build_memory_service
    from creativity_service.modules.memory.schemas import MemoryCreate, PolicyInput
    from tests.integration.core.conftest import TestAuthorization
    from tests.integration.memory.conftest import business_attributes

    env = agent_env
    memory = build_memory_service(env.engine, env.iam.authorization)
    await memory.set_policy(env.context, PolicyInput(revision=0, attributes=business_attributes()))
    contexts, memories = [], []
    for index in range(10):
        scoped = env.context.model_copy(
            update={
                "scope": env.context.scope.model_copy(
                    update={"subject_type": "user", "subject_id": f"query_subject_{index}"}
                )
            }
        )
        await RecoveryService(env.engine, TestAuthorization()).initialize_fresh(scoped)
        contexts.append(scoped)
        memories.append(
            await memory.create(
                scoped,
                MemoryCreate(
                    key="usual_budget", value={"min": index, "max": index + 100, "currency": "CNY"}
                ),
            )
        )
        if index == 0:
            with statements(env.engine) as single:
                assert len((await memory.list_memories(env.context)).items) == 1
    with statements(env.engine) as multiple:
        listed = await memory.list_memories(env.context)
    assert len(listed.items) == 10
    assert len(single) == len(multiple) <= 25
    own = await memory.list_memories(contexts[0])
    assert [item.memory_id for item in own.items] == [memories[0].memory_id]
    with pytest.raises(ServiceError) as denied:
        await memory.detail(contexts[0], memories[1].memory_id)
    assert denied.value.status in {403, 404}
