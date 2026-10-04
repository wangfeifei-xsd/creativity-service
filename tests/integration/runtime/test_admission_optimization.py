"""受理并发优化不改变额度、授权、删除与原子提交边界。"""

import asyncio

import pytest
from sqlalchemy import select

from creativity_service.core.context import TaskEnvelope
from creativity_service.core.database.tables import metadata as core_metadata
from creativity_service.core.locking import acquire_locks
from creativity_service.core.primitives import RunInput, ServiceError, digest
from creativity_service.modules.agents.repositories import repository
from creativity_service.modules.agents.schemas import AgentVersionEdit
from creativity_service.modules.budgets.schemas import (
    BudgetCreate,
    BudgetUpdate,
    PlatformLimitCreate,
)
from creativity_service.modules.runs.tables import metadata as run_metadata
from creativity_service.modules.usage.assembly import build_usage_services
from creativity_service.modules.usage.repositories import ledger_key, platform_key
from tests.integration.agents.test_agents import publish
from tests.integration.agents.test_read_queries import statements
from tests.integration.channels.test_prompts_http import MemoryStore
from tests.integration.runtime.test_execution import admitted

pytestmark = [
    pytest.mark.integration,
    pytest.mark.parametrize(
        "agent_env",
        [{"environment": "test", "independent_actions": ["release:publish"]}],
        indirect=True,
    ),
]


async def test_admitted_run_starts_while_channel_and_platform_admission_are_locked(runtime_env):
    env = runtime_env
    receipt, message = await admitted(env, purpose="production")
    async with env.engine.begin() as quota_writer:
        await acquire_locks(
            quota_writer, frozenset([platform_key(), ledger_key(env.context.scope.channel_id)])
        )
        async with asyncio.timeout(3):
            lease = await env.runs.claim_lease(message, "already-admitted")
        assert lease is not None and lease.run_id == receipt.run_id


async def test_admission_reuses_locked_authorization_and_deletion_reads(runtime_env):
    env = runtime_env
    detail = await env.agents.create(env.context, env.body)
    await publish(env, detail)
    with statements(env.engine) as queries:
        receipt = await env.runs.admit_run(
            env.context,
            RunInput(agent_code=env.body.agent_code, input={"request": "检查"}),
            "reads",
        )
    assert receipt.state == "QUEUED"
    for table in ("recovery_barriers", "deletion_markers"):
        assert sum(f"FROM {table}" in query for query in queries) == 1
    # 事务外入口授权和事务内提交复核各一次，提交事务内不得重复加载身份事实。
    for table in ("channels", "platform_accounts", "channel_memberships", "resource_grants"):
        assert (
            sum(
                f"FROM {table} " in query
                or query.endswith(f"FROM {table}")
                or f"JOIN {table} " in query
                for query in queries
            )
            == 2
        )


async def test_platform_rejection_rolls_back_candidate_run_and_outbox(runtime_env):
    env = runtime_env
    usage = build_usage_services(env.engine, env.services.channels, MemoryStore())
    await usage.management.platform_limits(
        env.admin,
        PlatformLimitCreate(limit_code="one", name="并发一", unit="concurrency", limit_value=1),
    )
    receipt, _ = await admitted(env, purpose="production")
    tables = [
        repository("agent_candidates", env.context.scope).table,
        core_metadata.tables["source_links"],
        core_metadata.tables["release_snapshots"],
        *[
            run_metadata.tables[name]
            for name in ("runs", "run_contents", "run_idempotency", "dispatch_outbox", "run_events")
        ],
    ]

    async def identifiers():
        async with env.engine.connect() as connection:
            return {
                table.name: set(await connection.scalars(select(table.c.id))) for table in tables
            }

    before = await identifiers()
    with pytest.raises(ServiceError) as rejected:
        await env.runs.admit_run(
            env.context,
            RunInput(agent_code=env.body.agent_code, input={"request": "超额"}),
            "rejected",
        )
    assert rejected.value.code == "PLATFORM_LIMIT_EXCEEDED"
    assert await identifiers() == before
    assert (
        await env.runs.load(
            TaskEnvelope(channel_id=env.context.scope.channel_id, run_id=receipt.run_id)
        )
    )["state"] == "QUEUED"


@pytest.mark.parametrize("change", ["logout", "publication"])
async def test_preparation_is_revalidated_before_commit(runtime_env, monkeypatch, change):
    env = runtime_env
    detail = await env.agents.create(env.context, env.body)
    published = await publish(env, detail)
    original = env.runs.resolver.prepare

    async def prepare(context, request):
        prepared = await original(context, request)
        if change == "logout":
            await env.iam.sessions.logout(env.tenant.manager)
        else:
            draft = next(v for v in published.versions if v.status.value == "DRAFT")
            definition = env.definition.model_copy(
                update={"limits": env.definition.limits.model_copy(update={"max_model_rounds": 4})}
            )
            await env.agents.edit_version(
                env.context,
                draft.version_id,
                AgentVersionEdit(revision=draft.revision, definition=definition),
            )
            await publish(env, await env.agents.detail(env.context, detail.agent.agent_id))
        return prepared

    monkeypatch.setattr(env.runs.resolver, "prepare", prepare)
    with pytest.raises(ServiceError) as rejected:
        await env.runs.admit_run(
            env.context, RunInput(agent_code=env.body.agent_code, input={"request": "竞态"}), "race"
        )
    assert rejected.value.code == ("UNAUTHENTICATED" if change == "logout" else "REVISION_CONFLICT")
    async with env.engine.connect() as connection:
        assert not list(await connection.scalars(select(run_metadata.tables["runs"].c.id)))


@pytest.mark.parametrize("limit_scope", ["channel", "platform"])
async def test_last_slot_remains_atomic_after_parallel_preparation(runtime_env, limit_scope):
    env = runtime_env
    usage = build_usage_services(env.engine, env.services.channels, MemoryStore())
    if limit_scope == "channel":
        await usage.management.save_budget(
            env.tenant.manager,
            BudgetCreate(
                name="渠道并发一",
                scope_type="channel",
                scope_id=env.context.scope.channel_id,
                unit="concurrency",
                limit_value="1",
            ),
        )
    else:
        await usage.management.platform_limits(
            env.admin,
            PlatformLimitCreate(
                limit_code="one", name="平台并发一", unit="concurrency", limit_value=1
            ),
        )
    detail = await env.agents.create(env.context, env.body)
    await publish(env, detail)
    request = RunInput(agent_code=env.body.agent_code, input={"request": "抢占最后名额"})
    results = await asyncio.gather(
        *(env.runs.admit_run(env.context, request, f"slot-{index}") for index in range(5)),
        return_exceptions=True,
    )
    assert sum(not isinstance(result, Exception) for result in results) == 1
    errors = [result for result in results if isinstance(result, Exception)]
    assert all(
        isinstance(error, ServiceError)
        and error.code in {"BUDGET_EXCEEDED", "PLATFORM_LIMIT_EXCEEDED"}
        and error.status == 429
        for error in errors
    )
    async with env.engine.connect() as connection:
        for name in ("runs", "run_idempotency", "dispatch_outbox"):
            assert len(list(await connection.scalars(select(run_metadata.tables[name].c.id)))) == 1


@pytest.mark.parametrize("legacy_publication", [False, True])
async def test_channel_concurrency_changes_apply_without_republishing(
    runtime_env, monkeypatch, legacy_publication
):
    env = runtime_env
    usage = build_usage_services(env.engine, env.services.channels, MemoryStore())
    body = BudgetCreate(
        name="渠道并发",
        scope_type="channel",
        scope_id=env.context.scope.channel_id,
        unit="concurrency",
        limit_value="1",
    )
    budget = await usage.management.save_budget(env.tenant.manager, body)
    detail = await env.agents.create(env.context, env.body)
    # 用原发布算法生成历史摘要，验证升级不要求批量改写或重发既有 Agent。
    with monkeypatch.context() as patch:
        if legacy_publication:
            import creativity_service.modules.agents.snapshots as snapshots

            original = snapshots.check_budget

            async def legacy_check(*args, **kwargs):
                policies = await original(*args, **kwargs)
                policy = (await usage.budgets.policies(args[1]))[0]
                return [
                    *policies,
                    {
                        **{
                            k: policy[k]
                            for k in (
                                "id",
                                "version_id",
                                "scope_type",
                                "scope_id",
                                "unit",
                                "mode",
                                "currency",
                            )
                        },
                        "limit_value": str(policy["limit_value"]),
                    },
                ]

            patch.setattr(snapshots, "check_budget", legacy_check)
        published = await publish(env, detail)
    version = next(v for v in published.versions if v.status.value == "PUBLISHED")
    async with env.engine.connect() as connection:
        before = await repository("resource_versions", env.context.scope).get(
            connection, version.version_id
        )
    request = RunInput(agent_code=env.body.agent_code, input={"request": "新并发配置"})
    first = await env.runs.admit_run(env.context, request, "limit-first")
    budget = await usage.management.save_budget(
        env.tenant.manager,
        BudgetUpdate(**{**body.model_dump(), "revision": budget.revision, "limit_value": "2"}),
        budget.id,
    )
    second = await env.runs.admit_run(env.context, request, "limit-second")
    assert first.run_id != second.run_id
    with pytest.raises(ServiceError) as full:
        await env.runs.admit_run(env.context, request, "limit-full")
    assert full.value.code == "BUDGET_EXCEEDED" and full.value.status == 429
    await usage.management.save_budget(
        env.tenant.manager,
        BudgetUpdate(**{**body.model_dump(), "revision": budget.revision, "status": "DISABLED"}),
        budget.id,
    )
    await env.runs.admit_run(env.context, request, "limit-disabled")
    async with env.engine.connect() as connection:
        assert (
            await repository("resource_versions", env.context.scope).get(
                connection, version.version_id
            )
            == before
        )
    # 兼容逻辑只允许实时渠道并发变化，不能忽略其他依赖摘要差异。
    from creativity_service.core.database import transaction
    from creativity_service.core.locking import read_key, record_key
    from creativity_service.modules.releases.checks import published_dependencies_match

    async with transaction(
        env.engine,
        env.context.scope,
        [
            read_key(
                record_key(env.context.scope.channel_id, "resource_versions", version.version_id)
            )
        ],
    ) as uow:
        assert not await published_dependencies_match(
            uow,
            before,
            {"versions": [{"content_digest": digest("changed")}], "policies": {"budgets": []}},
        )
