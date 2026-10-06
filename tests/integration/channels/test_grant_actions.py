"""资源授权行操作复用真实策略；列表扩容不能增加逐行查询。"""

from collections import Counter
from contextlib import contextmanager

import pytest
from sqlalchemy import event

from creativity_service.core.database import transaction
from creativity_service.core.locking import record_key
from creativity_service.core.primitives import ServiceError
from creativity_service.modules.iam.repositories import policy_key, save
from creativity_service.modules.iam.schemas import GrantInput

from .conftest import provision

pytestmark = pytest.mark.integration


async def stored_grant(env, tenant, identifier, *, revision=None, **values):
    channel_id = tenant.channel.channel_id
    async with transaction(
        env.engine,
        tenant.manager.context.scope,
        [policy_key(channel_id), record_key(channel_id, "resource_grants", identifier)],
    ) as uow:
        return await save(uow, "resource_grants", identifier, values, revision)


def grant_input(tenant, **values):
    return GrantInput(
        **{
            "grantee_type": "role",
            "grantee_id": "builder",
            "resource_type": "version",
            "resource_id": "*",
            "allowed_actions": ["version:read"],
            "environments": ["test"],
            "data_scopes": [tenant.domain.data_scope_id],
            **values,
        }
    )


def enabled(row):
    assert {action.action_key for action in row.actions} == {"grant:edit", "grant:revoke"}
    return all(action.enabled for action in row.actions)


async def test_grant_actions_match_delegation_and_writes_recheck_latest_policy(channel_env):
    env = channel_env
    tenant = await provision(env)
    access, channel_id = env.iam.access, tenant.channel.channel_id
    body = grant_input(tenant)
    created = await access.put_grant(tenant.manager, channel_id, "manageable", body)
    assert enabled(created)
    excessive = grant_input(tenant, resource_type="agent", allowed_actions=["release:publish"])
    await stored_grant(env, tenant, "excessive", **excessive.model_dump(exclude={"revision"}))
    listed = {row.grant_id: row for row in await access.list_grants(tenant.manager, channel_id)}
    assert enabled(listed["manageable"])
    assert not enabled(listed["excessive"])
    assert {action.disabled_reason for action in listed["excessive"].actions} == {
        "不能超出本人的可授权范围"
    }
    # 修改为更小的动作集合，也不能绕过对原记录完整授权范围的检查。
    with pytest.raises(ServiceError, match="不能超出本人的可授权范围"):
        await access.put_grant(
            tenant.manager,
            channel_id,
            "excessive",
            excessive.model_copy(update={"revision": 1, "allowed_actions": ["version:read"]}),
        )
    with pytest.raises(ServiceError, match="不能超出本人的可授权范围"):
        await access.revoke_grant(tenant.manager, channel_id, "excessive", 1)
    edited = await access.put_grant(
        tenant.manager, channel_id, "manageable", body.model_copy(update={"revision": 1})
    )
    assert enabled(edited)
    await access.revoke_grant(tenant.manager, channel_id, "manageable", edited.revision)
    assert "manageable" not in {
        row.grant_id for row in await access.list_grants(tenant.manager, channel_id)
    }
    # 旧页面曾显示可操作，权限收回后直接请求也必须失败；下一次读取显示禁用。
    initial = next(row for key, row in listed.items() if key.startswith("initial_"))
    updated = await stored_grant(
        env, tenant, initial.grant_id, revision=initial.revision, allowed_actions=["grant:read"]
    )
    read_only = await access.list_grants(tenant.manager, channel_id)
    assert read_only and all(not enabled(row) for row in read_only)
    assert all(
        action.disabled_reason == "没有资源授权管理权限"
        for row in read_only
        for action in row.actions
    )
    with pytest.raises(ServiceError) as denied:
        await access.revoke_grant(tenant.manager, channel_id, "excessive", 1)
    assert denied.value.status == 403
    await stored_grant(
        env, tenant, initial.grant_id, revision=updated["revision"], allowed_actions=[]
    )
    with pytest.raises(ServiceError) as denied:
        await access.list_grants(tenant.manager, channel_id)
    assert denied.value.status == 403


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


async def test_grant_list_reuses_policy_without_directory_workflow_or_n_plus_one(
    channel_env, monkeypatch
):
    env = channel_env
    tenant = await provision(env)
    access, channel_id = env.iam.access, tenant.channel.channel_id

    async def forbidden_directory(*args, **kwargs):
        pytest.fail("授权列表不能调用全渠道目录查询流程")

    monkeypatch.setattr(access.directory, "list_for", forbidden_directory)
    monkeypatch.setattr(access.directory, "data_for", forbidden_directory)
    body = grant_input(tenant, resource_type="client", resource_id="client_0")
    await stored_grant(env, tenant, "grant_0", **body.model_dump(exclude={"revision"}))
    with statements(env.engine) as single:
        before = await access.list_grants(tenant.manager, channel_id)
    for index in range(1, 21):
        body = grant_input(tenant, resource_type="client", resource_id=f"client_{index}")
        await stored_grant(env, tenant, f"grant_{index}", **body.model_dump(exclude={"revision"}))
    with statements(env.engine) as multiple:
        after = await access.list_grants(tenant.manager, channel_id)
    assert len(after) == len(before) + 20
    # 多个具体资源统一批量读取，授权与资源名称查询均不能随记录条数增长。
    assert len(multiple) == len(single) <= 25
    counts = Counter(
        table
        for statement in multiple
        for table in ("channel_memberships", "resource_grants", "service_clients")
        if f"FROM {table}" in statement
    )
    assert counts == {"channel_memberships": 1, "resource_grants": 1, "service_clients": 1}
    await env.iam.sessions.logout(tenant.manager)
    with pytest.raises(ServiceError) as denied:
        await access.list_grants(tenant.manager, channel_id)
    assert denied.value.status == 401
