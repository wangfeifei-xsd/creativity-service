"""AGT-A18：真实 MySQL 验证停用删除、当前业务引用、跨环境权限和历史保留。"""

import asyncio

import pytest

from creativity_service.core.database import transaction
from creativity_service.core.primitives import RunInput, ServiceError
from creativity_service.modules.agents.repositories import repository
from creativity_service.modules.agents.schemas import AgentDeleteInput, AgentStateInput
from creativity_service.modules.channels.schemas import EnvironmentCreate
from creativity_service.modules.iam.schemas import ChannelContextInput
from creativity_service.modules.integrations.automation import keys, repo
from creativity_service.modules.integrations.automation_schemas import (
    BatchCreate,
    BatchItem,
    ScheduleCreate,
    ScheduleUpdate,
    Toggle,
)
from creativity_service.modules.releases.services import ReleaseService
from creativity_service.modules.resources.configuration import reference_rows
from creativity_service.workers.executor import execute_message
from tests.integration.agents.test_agents import publish
from tests.integration.channels.conftest import login, provision
from tests.integration.channels.test_grant_actions import stored_grant
from tests.integration.runtime.test_execution import admitted

pytestmark = pytest.mark.integration


async def stop(env, detail, operation="offline", context=None):
    return await ReleaseService(env.agents).state(
        context or env.context,
        detail.agent.agent_id,
        AgentStateInput(revision=detail.agent.revision, operation=operation, reason="不再使用"),
    )


def delete_action(detail):
    return next(action for action in detail.agent.actions if action.action_key == "delete")


async def delete(env, detail):
    await env.agents.delete(
        env.context, detail.agent.agent_id, AgentDeleteInput(revision=detail.agent.revision)
    )


def batch_body(code, event="delete-check"):
    return BatchCreate(
        name="删除引用校验",
        items=[
            BatchItem(event_id=event, request=RunInput(agent_code=code, input={"request": "检查"}))
        ],
    )


@pytest.mark.parametrize("operation", ["offline", "emergency_stop"])
async def test_stopped_agent_delete_http_keeps_records_and_releases_dependencies(
    agent_env, operation
):
    env = agent_env
    detail = await env.agents.create(env.context, env.body)
    assert not delete_action(detail).enabled
    with pytest.raises(ServiceError) as enabled:
        await delete(env, detail)
    assert enabled.value.code == "AGENT_NOT_STOPPED"
    stopped = await stop(env, detail, operation)
    assert delete_action(stopped).enabled
    listed = await env.agents.list_agents(env.context)
    assert next(a for a in listed.items[0].actions if a.action_key == "delete").enabled
    async with env.engine.connect() as connection:
        assert await reference_rows(connection, env.context.scope, [env.prompt_id])
    path = f"/admin/v1/agents/{detail.agent.agent_id}"
    stale = await env.client.request("DELETE", path, json={"revision": detail.agent.revision})
    assert stale.status_code == 409 and stale.json()["error"]["code"] == "REVISION_CONFLICT"
    response = await env.client.request("DELETE", path, json={"revision": stopped.agent.revision})
    assert response.status_code == 204 and not response.content
    assert not (await env.agents.list_agents(env.context)).items
    assert (await env.client.get(path)).status_code >= 400
    assert (
        await env.client.get(f"/admin/v1/agent-versions/{detail.versions[0].version_id}")
    ).status_code >= 400
    with pytest.raises(ServiceError):
        await stop(env, stopped, "enable")
    with pytest.raises(ServiceError) as reused:
        await env.agents.create(env.context, env.body)
    assert reused.value.code == "AGENT_CODE_CONFLICT"
    async with env.engine.connect() as connection:
        row = await repository("agents", env.context.scope, include_deleted=True).get(
            connection, detail.agent.agent_id
        )
        assert row["is_deleted"] and row["name"] == detail.agent.name
        assert await repository("resource_versions", env.context.scope).get(
            connection, detail.versions[0].version_id
        )
        assert not await reference_rows(connection, env.context.scope, [env.prompt_id])
        events = await repository("audit_events", env.context.scope).find(
            connection, target_id=detail.agent.agent_id, action="agent.delete"
        )
        assert len(events) == 1


@pytest.mark.parametrize("purpose", ["production", "debug"])
async def test_agent_delete_blocks_unfinished_run_and_preserves_completed_history(
    runtime_env, purpose
):
    env = runtime_env
    receipt, message = await admitted(env, purpose=purpose)
    detail = (await env.agents.list_agents(env.context)).items[0]
    stopped = await stop(env, await env.agents.detail(env.context, detail.agent_id))
    assert not delete_action(stopped).enabled
    with pytest.raises(ServiceError) as running:
        await delete(env, stopped)
    assert running.value.code == "AGENT_RUNNING"
    await execute_message(env.runs, message, "history-worker", env.runtime)
    before = await env.runs.get_run(env.context, receipt.run_id)
    assert before.state == "SUCCEEDED", before.error
    history = await env.runs.detail(env.context, receipt.run_id)
    stopped = await env.agents.detail(env.context, detail.agent_id)
    assert delete_action(stopped).enabled
    await delete(env, stopped)
    after = await env.runs.get_run(env.context, receipt.run_id)
    assert after.result == before.result and after.usage_summary == before.usage_summary
    assert await env.runs.detail(env.context, receipt.run_id) == history
    assert not (await env.agents.list_agents(env.context, published_only=True)).items
    with pytest.raises(ServiceError):
        await env.runs.admit_run(
            env.context,
            RunInput(agent_code=env.body.agent_code, input={"request": "新调用"}),
            "after-delete",
        )
    async with env.engine.connect() as connection:
        mapping = await repository("release_mappings", env.context.scope, include_deleted=True).get(
            connection, env.agents.mapping_id(env.context, detail.agent_id)
        )
        if purpose == "production":
            assert mapping["is_deleted"]
        versions = await repository("resource_versions", env.context.scope).find(
            connection, resource_type="agent", resource_id=detail.agent_id
        )
        assert versions


@pytest.mark.parametrize("active", [True, False])
async def test_agent_delete_requires_rebinding_active_and_paused_schedules(runtime_env, active):
    env = runtime_env
    detail = await publish(env, await env.agents.create(env.context, env.body))
    service = env.runs.automation
    body = ScheduleCreate(
        name="业务定时计划",
        request=RunInput(agent_code=env.body.agent_code, input={"request": "检查"}),
        interval_seconds=60,
    )
    schedule = await service.create_schedule(env.context, body)
    if not active:
        schedule = await service.toggle_schedule(
            env.context, schedule["id"], Toggle(revision=schedule["revision"], active=False)
        )
    stopped = await stop(env, detail)
    with pytest.raises(ServiceError) as used:
        await delete(env, stopped)
    assert used.value.code == "AGENT_IN_USE"
    assert "定时计划" in delete_action(stopped).disabled_reason
    replacement = await env.agents.create(
        env.context, env.body.model_copy(update={"agent_code": "replacement"})
    )
    await publish(env, replacement)
    await service.update_schedule(
        env.context,
        schedule["id"],
        ScheduleUpdate(
            **body.model_copy(
                update={"request": body.request.model_copy(update={"agent_code": "replacement"})}
            ).model_dump(),
            revision=schedule["revision"],
        ),
    )
    await delete(env, stopped)
    assert (await service.list_schedules(env.context))[0]["spec"]["request"][
        "agent_code"
    ] == "replacement"


@pytest.mark.parametrize("state", ["PENDING", "CLAIMED"])
async def test_agent_delete_blocks_pending_items_and_prevents_deleted_batch_retry(
    runtime_env, state
):
    env = runtime_env
    detail = await stop(env, await env.agents.create(env.context, env.body))
    service = env.runs.automation
    body = batch_body(env.body.agent_code)
    batch = await service.create_batch(env.context, body, "batch-before-delete")
    item = batch.items[0]
    if state == "CLAIMED":
        async with transaction(
            env.engine, env.context.scope, keys(env.context, ("automation_items", item.item_id))
        ) as uow:
            await repo(env.context.scope, "automation_items").change(
                uow, item.item_id, item.revision, {"state": "CLAIMED"}
            )
    with pytest.raises(ServiceError) as pending:
        await delete(env, detail)
    assert pending.value.code == "AGENT_IN_USE"
    # 模拟派发失败这一合法终态，失败历史不阻止删除，但删除后不允许再次入队。
    async with transaction(
        env.engine, env.context.scope, keys(env.context, ("automation_items", item.item_id))
    ) as uow:
        row = await repo(env.context.scope, "automation_items").get(uow.connection, item.item_id)
        failed = await repo(env.context.scope, "automation_items").change(
            uow, item.item_id, row["revision"], {"state": "FAILED"}
        )
    await delete(env, detail)
    with pytest.raises(ServiceError) as retried:
        await service.retry_item(env.context, item.item_id, failed["revision"])
    assert retried.value.code == "AGENT_DELETED"
    with pytest.raises(ServiceError) as new_batch:
        await service.create_batch(
            env.context, batch_body(env.body.agent_code, "after-delete"), "batch-after-delete"
        )
    assert new_batch.value.code == "AGENT_DELETED"
    historical = await service.create_batch(env.context, body, "batch-before-delete")
    assert historical.batch_id == batch.batch_id and historical.items[0].state == "FAILED"
    assert len(await service.list_batches(env.context)) == 1


async def test_agent_delete_checks_other_environments_and_management_permissions(runtime_env):
    env = runtime_env
    detail = await publish(env, await env.agents.create(env.context, env.body))
    await env.services.channels.create_environment(
        env.admin, env.context.scope.channel_id, EnvironmentCreate(environment="prod", name="生产")
    )
    _, session = await login(env)
    current_token = await env.iam.sessions.enter(
        session, ChannelContextInput(channel_id=env.context.scope.channel_id, environment="test")
    )
    env.tenant.manager = await env.iam.authentication.admin_session(
        current_token.access_token, "test-delete-check"
    )
    env.context = env.tenant.manager.context
    _, session = await login(env)
    token = await env.iam.sessions.enter(
        session,
        ChannelContextInput(channel_id=env.context.scope.channel_id, environment="prod"),
    )
    prod = (
        await env.iam.authentication.admin_session(token.access_token, "prod-delete-check")
    ).context
    detail = await stop(env, detail, "enable", context=prod)
    stopped = await stop(env, detail)
    with pytest.raises(ServiceError) as enabled:
        await delete(env, stopped)
    assert enabled.value.code == "AGENT_NOT_STOPPED"
    assert "其他环境" in delete_action(stopped).disabled_reason
    stopped = await stop(env, stopped, "emergency_stop", context=prod)
    batch = await env.runs.automation.create_batch(
        prod, batch_body(env.body.agent_code), "prod-batch"
    )
    with pytest.raises(ServiceError) as referenced:
        await delete(env, stopped)
    assert referenced.value.code == "AGENT_IN_USE"
    await env.runs.automation.cancel_batch(prod, batch.batch_id, batch.revision, False)
    grants = await env.iam.access.list_grants(env.tenant.manager, env.context.scope.channel_id)
    initial = next(g for g in grants if g.grant_id.startswith("initial_"))
    restricted = await stored_grant(
        env, env.tenant, initial.grant_id, revision=initial.revision, environments=["test"]
    )
    blocked = await env.agents.detail(env.context, detail.agent.agent_id)
    assert not delete_action(blocked).enabled
    with pytest.raises(ServiceError) as forbidden:
        await delete(env, blocked)
    assert forbidden.value.code == "FORBIDDEN"
    await stored_grant(
        env,
        env.tenant,
        initial.grant_id,
        revision=restricted["revision"],
        environments=initial.environments,
    )
    await delete(env, blocked)


async def test_agent_delete_rejects_foreign_channel_and_revoked_permission(agent_env):
    env = agent_env
    detail = await stop(env, await env.agents.create(env.context, env.body))
    foreign = await provision(env, "other")
    with pytest.raises(ServiceError) as cross_channel:
        await env.agents.delete(
            foreign.manager.context,
            detail.agent.agent_id,
            AgentDeleteInput(revision=detail.agent.revision),
        )
    assert cross_channel.value.status in {403, 404}
    grants = await env.iam.access.list_grants(env.tenant.manager, env.context.scope.channel_id)
    initial = next(g for g in grants if g.grant_id.startswith("initial_"))
    await stored_grant(
        env, env.tenant, initial.grant_id, revision=initial.revision, allowed_actions=[]
    )
    with pytest.raises(ServiceError) as forbidden:
        await delete(env, detail)
    assert forbidden.value.code == "FORBIDDEN"


@pytest.mark.parametrize("competitor", ["enable", "batch"])
async def test_agent_delete_serializes_with_enable_and_new_business_references(
    runtime_env, competitor
):
    env = runtime_env
    detail = await stop(env, await env.agents.create(env.context, env.body))
    operation = (
        stop(env, detail, "enable")
        if competitor == "enable"
        else env.runs.automation.create_batch(env.context, batch_body(env.body.agent_code), "race")
    )
    results = await asyncio.gather(delete(env, detail), operation, return_exceptions=True)
    assert sum(isinstance(result, ServiceError) for result in results) == 1, results
    async with env.engine.connect() as connection:
        agent = await repository("agents", env.context.scope, include_deleted=True).get(
            connection, detail.agent.agent_id
        )
        pending = await repo(env.context.scope, "automation_items").find(
            connection, state="PENDING"
        )
    if agent["is_deleted"]:
        assert not pending
        assert isinstance(results[1], ServiceError)
    else:
        assert isinstance(results[0], ServiceError)
        current = await env.agents.detail(env.context, detail.agent.agent_id)
        assert not delete_action(current).enabled
