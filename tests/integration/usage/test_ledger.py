"""USG-A01—A08/A10：真实事务、累计回调、未知调用与渠道隔离验收。"""

import asyncio
from datetime import timedelta
from decimal import Decimal

import pytest
from sqlalchemy import update

from creativity_service.core.contracts import UsageEvent
from creativity_service.core.database import transaction
from creativity_service.core.primitives import ServiceError, new_id, utcnow
from creativity_service.modules.budgets.schemas import (
    BudgetCreate,
    BudgetUpdate,
    PlatformLimitCreate,
)
from creativity_service.modules.usage.repositories import (
    budget_configuration_key,
    ledger_key,
    rows,
    save,
)
from creativity_service.modules.usage.schemas import AttemptPlan, UsageFilter
from creativity_service.modules.usage.tables import metadata
from tests.integration.channels.conftest import credential, provision

pytestmark = pytest.mark.integration


def plan(run=None, attempt=None, **values):
    return AttemptPlan(
        run_id=run or new_id("run"),
        attempt_id=attempt or new_id("attempt"),
        agent_id="shared_agent",
        model_id="model_demo",
        connection_id="connection_demo",
        purpose="production",
        input_tokens=100,
        max_output_tokens=100,
        names={"model": "测试模型", "agent": "共享智能体"},
        **values,
    )


async def price(env, currency="USD", rate="1", relations=None, model="model_demo"):
    async with transaction(
        env.engine,
        env.scope,
        [ledger_key(env.scope.channel_id), budget_configuration_key(env.scope.channel_id)],
    ) as uow:
        return await save(
            uow,
            "price_versions",
            new_id("price"),
            {
                "name": "测试价格",
                "model_id": model,
                "currency": currency,
                "price_items": [
                    {"dimension": dimension, "amount": rate, "per_units": 100}
                    for dimension in ("input", "output")
                ],
                "subset_relations": relations or {},
                "unit": "每百 Token",
                "source": "03 事件夹具",
                "effective_at": utcnow() - timedelta(days=1),
            },
        )


async def budget(env, unit="amount", limit="2", mode="HARD"):
    return await env.usage.management.save_budget(
        env.channel.manager,
        BudgetCreate(
            name="渠道限额",
            scope_type="channel",
            scope_id=env.scope.channel_id,
            unit=unit,
            currency="USD" if unit == "amount" else None,
            limit_value=limit,
            mode=mode,
        ),
    )


async def admit(env, p, context=None):
    context = context or env.context
    async with transaction(
        env.engine, context.scope, env.usage.budgets.admission_keys(context, p.run_id)
    ) as uow:
        return await env.usage.budgets.admit(uow, context, p.run_id, p)


async def prepare(env, p=None, send=True, context=None):
    p = p or plan()
    context = context or env.context
    await admit(env, p, context)
    await env.usage.budgets.reserve_attempt(context, p)
    if send:
        await env.usage.ledger.mark_sent(context.scope, p.attempt_id)
    return p


def event(env, p, version=1, status="REPORTED", final=True, tokens=None, scope=None):
    return UsageEvent(
        scope=scope or env.scope,
        attempt_id=p.attempt_id,
        connection_id=p.connection_id,
        source_request_id="provider_" + p.attempt_id,
        event_version=version,
        status=status,
        raw_usage={"input_tokens": 50, "output_tokens": 20},
        normalized_tokens={}
        if status == "MISSING"
        else (tokens if tokens is not None else {"input": 50, "output": 20}),
        subset_relations=p.subset_relations,
        cumulative=True,
        final=final,
        observed_at=utcnow(),
    )


async def stored(env, name, **filters):
    async with env.engine.connect() as connection:
        return await rows(connection, name, env.scope.channel_id, **filters)


async def test_concurrent_last_allowance_and_atomic_multi_policy_rollback(usage_env):
    env = usage_env
    await price(env)
    await budget(env)
    p1, p2 = plan(), plan()
    await admit(env, p1)
    await admit(env, p2)
    result = await asyncio.gather(
        env.usage.budgets.reserve_attempt(env.context, p1),
        env.usage.budgets.reserve_attempt(env.context, p2),
        return_exceptions=True,
    )
    assert sum(not isinstance(r, Exception) for r in result) == 1
    assert next(r for r in result if isinstance(r, ServiceError)).code == "BUDGET_EXCEEDED"
    assert len(await stored(env, "budget_reservations")) == 1
    assert len(await stored(env, "usage_records")) == 1


async def test_cumulative_dedup_final_precedence_and_late_correction(usage_env):
    env = usage_env
    await price(env)
    await budget(env, limit="10")
    p = await prepare(env)
    estimated = event(env, p, version=7, status="ESTIMATED", tokens={"input": 80, "output": 80})
    await env.usage.ledger.settle(estimated, outcome="UNKNOWN")
    assert await env.usage.ledger.release_unused(env.scope, p.attempt_id) == "PENDING"
    await env.usage.budgets.finish_admission(env.context, p.run_id)
    reported = event(env, p, version=2, tokens={"input": 50, "output": 20})
    results = await asyncio.gather(*(env.usage.ledger.settle(reported) for _ in range(5)))
    assert {r["amount"] for r in results} == {Decimal("0.7")}
    assert len(await stored(env, "usage_events")) == 2
    assert len(await stored(env, "usage_adjustments")) == 2
    assert (await stored(env, "admissions"))[0]["status"] == "RELEASED"
    assert (await stored(env, "budget_reservations"))[0]["settled_amount"] == Decimal("0.7")
    older = event(env, p, version=1, tokens={"input": 10, "output": 5})
    await env.usage.ledger.settle(older)
    assert (await stored(env, "usage_records"))[0]["amount"] == Decimal("0.7")
    conflict = reported.model_copy(update={"normalized_tokens": {"input": 900, "output": 900}})
    with pytest.raises(ServiceError, match="冲突"):
        await env.usage.ledger.settle(conflict)


async def test_expired_sent_process_exit_and_idempotent_compensation(usage_env):
    env = usage_env
    await price(env)
    await budget(env, limit="10")
    sent = await prepare(env)
    unsent = await prepare(env, send=False)
    # 已提交发送意图后模拟进程退出，不再调用任何完成钩子。
    table = metadata.tables["usage_records"]
    async with env.engine.begin() as connection:
        await connection.execute(
            update(table)
            .where(table.c.channel_id == env.scope.channel_id)
            .values(created_at=utcnow() - timedelta(hours=1))
        )
    async with env.engine.begin() as connection:
        reservations = metadata.tables["budget_reservations"]
        await connection.execute(
            update(reservations)
            .where(reservations.c.channel_id == env.scope.channel_id)
            .values(expires_at=utcnow() - timedelta(minutes=1))
        )
    first = await env.usage.ledger.compensate(env.scope)
    second = await env.usage.ledger.compensate(env.scope)
    assert first == {"released": 1, "pending": 1}
    assert second == {"released": 0, "pending": 1}
    record = {r["attempt_id"]: r for r in await stored(env, "usage_records")}
    assert record[sent.attempt_id]["state"] == "PENDING"
    assert record[sent.attempt_id]["amount"] is None
    assert record[unsent.attempt_id]["state"] == "RELEASED"
    p = plan()
    await prepare(env, p)
    await env.usage.ledger.settle(event(env, sent), outcome="FAILED")
    assert (await stored(env, "usage_records", attempt_id=sent.attempt_id))[0]["state"] == "SETTLED"


async def test_multiple_attempts_currency_and_immutable_source(usage_env):
    env = usage_env
    await price(env)
    first = await prepare(env)
    await env.usage.ledger.settle(event(env, first), outcome="FAILED")
    await price(env, currency="CNY")
    fallback = plan(run=first.run_id)
    await env.usage.budgets.reserve_attempt(env.context, fallback)
    await env.usage.ledger.mark_sent(env.scope, fallback.attempt_id)
    await env.usage.ledger.settle(event(env, fallback), outcome="SUCCEEDED")
    query = UsageFilter(
        start_at=utcnow() - timedelta(days=1), end_at=utcnow() + timedelta(minutes=1)
    )
    summary = await env.usage.queries.summary(env.scope.channel_id, [env.scope], query)
    assert (summary.requests, summary.attempts, summary.input_tokens, summary.output_tokens) == (
        1,
        2,
        100,
        40,
    )
    assert {c.currency for c in summary.costs} == {"USD", "CNY"}
    assert summary.success_rate == "0.5000"
    rebuilt = await env.usage.queries.rebuild(env.scope, [env.scope], query)
    assert rebuilt.attempts == summary.attempts
    await env.usage.queries.rebuild(env.scope, [env.scope], query)
    assert len(await stored(env, "usage_aggregates")) == 1


async def test_growing_tool_context_rechecks_and_unpriced_is_not_zero(usage_env):
    env = usage_env
    await price(env)
    await budget(env)
    p = await prepare(env, send=False)
    larger = p.model_copy(update={"input_tokens": 250})
    with pytest.raises(ServiceError) as rejected:
        await env.usage.budgets.reserve_attempt(env.context, larger)
    assert rejected.value.code == "BUDGET_EXCEEDED"
    assert (await stored(env, "budget_reservations"))[0]["reserved_amount"] == Decimal(2)
    no_price = plan().model_copy(update={"model_id": "unpriced"})
    with pytest.raises(ServiceError) as rejected:
        await admit(env, no_price)
    assert rejected.value.code == "BUDGET_PRICE_REQUIRED"
    env.usage.budgets.available = False
    with pytest.raises(ServiceError) as rejected:
        await admit(env, plan())
    assert rejected.value.code == "BUDGET_CONTROL_UNAVAILABLE"


async def test_channel_key_rotation_debug_evaluation_and_cross_scope_rejected(usage_env):
    env = usage_env
    other = await provision(env, "playmate", "club")
    first_key = await credential(env, env.channel, "渠道服务一")
    next_key = await credential(env, env.channel, "渠道服务二")
    await price(env)
    for identity in (first_key, next_key):
        context = identity.context.model_copy(update={"scope": env.scope})
        p = await prepare(env, context=context)
        await env.usage.ledger.settle(event(env, p))
    for purpose in ("debug", "evaluation"):
        p = await prepare(env, plan().model_copy(update={"purpose": purpose}))
        await env.usage.ledger.settle(event(env, p))
    p = plan()
    await prepare(env, p, context=other.manager.context)
    await env.usage.ledger.settle(event(env, p, scope=other.manager.context.scope))
    records = await stored(env, "usage_records")
    assert len(records) == 4
    assert {r["key_id"] for r in records if r["key_id"]} == {
        first_key.context.key_id,
        next_key.context.key_id,
    }
    assert {r["purpose"] for r in records} == {"production", "debug", "evaluation"}
    with pytest.raises(ServiceError):
        await env.usage.ledger.settle(event(env, p))
    response = await env.client.get(
        f"/admin/v1/usage/records/{records[0]['id']}",
        headers={"Authorization": "Bearer " + other.token.access_token},
    )
    assert response.status_code == 404


async def test_platform_count_atomic_and_release_only_concurrency(usage_env):
    env = usage_env
    await env.usage.management.platform_limits(
        env.admin,
        PlatformLimitCreate(
            limit_code="parallel", name="平台并发", unit="concurrency", limit_value=1
        ),
    )
    a, b = plan(), plan()
    result = await asyncio.gather(admit(env, a), admit(env, b), return_exceptions=True)
    assert sum(isinstance(r, ServiceError) for r in result) == 1
    held = next(r for r in result if not isinstance(r, Exception))
    await env.usage.budgets.finish_admission(env.context, held.run_id)
    await admit(env, plan())
    async with env.engine.connect() as connection:
        occupancies = await rows(connection, "platform_quota_occupancies", "system")
    assert {r["channel_id"] for r in occupancies} == {"system"}
    assert {r["target_channel_id"] for r in occupancies} == {env.scope.channel_id}


async def test_export_real_permissions_and_csv_formula_protection(usage_env):
    env = usage_env
    p = await prepare(
        env, plan().model_copy(update={"names": {"model": "=恶意公式", "agent": "正常智能体"}})
    )
    await env.usage.ledger.settle(event(env, p))
    query = UsageFilter(
        start_at=utcnow() - timedelta(days=1), end_at=utcnow() + timedelta(minutes=1)
    )
    # 渠道管理员的 data:export 为独立权限，默认必须拒绝。
    with pytest.raises(ServiceError):
        await env.usage.exports.create(env.channel.manager, query)
    from tests.integration.channels.conftest import channel_body

    body = channel_body(env, "exporter").model_copy(update={"independent_actions": ["data:export"]})
    ch = await env.services.channels.create(env.admin, body)
    from creativity_service.modules.iam.schemas import ChannelContextInput

    token = await env.iam.sessions.enter(
        env.admin,
        ChannelContextInput(
            channel_id=ch.channel_id,
            environment="test",
            data_scope_id=(await env.services.channels.data_scopes(env.admin, ch.channel_id))[
                0
            ].data_scope_id,
        ),
    )
    session = await env.iam.authentication.admin_session(
        token.access_token, new_id("request"), governance=True
    )
    env.scope, env.context, env.channel.manager = session.context.scope, session.context, session
    p = await prepare(env, plan().model_copy(update={"names": {"model": "=恶意公式"}}))
    await env.usage.ledger.settle(event(env, p))
    job = await env.usage.exports.create(session, query)
    await env.usage.exports.process(env.scope.channel_id, job.id)
    await env.usage.exports.process(env.scope.channel_id, job.id)
    data = await env.usage.exports.download(session, job.id)
    assert "'=恶意公式" in data.decode("utf-8-sig")
    assert len(env.store.values) == 1
    assert (await env.usage.exports.list_exports(session))[0].metadata["unpriced"] == 1


async def test_budget_versions_alert_resolve_retrigger_and_repricing(usage_env):
    env = usage_env
    old_price = await price(env)
    policy = await budget(env)
    p = await prepare(env, send=False)
    alerts = await stored(env, "budget_alerts")
    assert len(alerts) == 2
    assert all(r["status"] == "ACTIVE" for r in alerts)
    await env.usage.ledger.release_unused(env.scope, p.attempt_id)
    assert all(r["status"] == "RESOLVED" for r in await stored(env, "budget_alerts"))
    next_attempt = await prepare(env)
    assert len(await stored(env, "budget_alerts")) == 2
    assert all(len(r["transitions"]) == 3 for r in await stored(env, "budget_alerts"))
    row = await env.usage.ledger.settle(event(env, next_attempt))
    replacement = await price(env, rate="2")
    revised = await env.usage.ledger.reprice(
        env.scope, row["id"], replacement["id"], row["revision"]
    )
    assert revised["amount"] == Decimal("1.4")
    corrections = await stored(env, "usage_adjustments", usage_id=row["id"])
    assert corrections[-1]["calculation"]["before"]["price_version_id"] == old_price["id"]
    updated = await env.usage.management.save_budget(
        env.channel.manager,
        BudgetUpdate(
            **{k: getattr(policy, k) for k in BudgetCreate.model_fields if k != "limit_value"},
            limit_value="4",
            revision=policy.revision,
        ),
        policy.id,
    )
    assert updated.version_id != policy.version_id
    assert (await stored(env, "budget_reservations", attempt_id=next_attempt.attempt_id))[0][
        "policy_version_id"
    ] == policy.version_id


async def test_all_admission_writes_rollback_with_later_limit_failure(usage_env):
    env = usage_env
    await env.usage.management.platform_limits(
        env.admin,
        PlatformLimitCreate(limit_code="requests", name="平台请求", unit="requests", limit_value=1),
    )
    await env.usage.management.platform_limits(
        env.admin,
        PlatformLimitCreate(
            limit_code="parallel", name="平台并发", unit="concurrency", limit_value=2
        ),
    )
    await admit(env, plan())
    with pytest.raises(ServiceError):
        await admit(env, plan())
    async with env.engine.connect() as connection:
        quota = await rows(connection, "platform_quota_occupancies", "system")
    assert len(quota) == 2
    assert len(await stored(env, "admissions")) == 1


async def test_no_price_token_budget_and_fixture_usage_missing_is_not_zero(usage_env):
    env = usage_env
    await budget(env, unit="tokens", limit="400")
    p = await prepare(env)
    missing = event(env, p, status="MISSING").model_copy(update={"normalized_tokens": {}})
    await env.usage.ledger.settle(missing)
    record = (await stored(env, "usage_records"))[0]
    assert (
        record["amount"] is None and record["input_tokens"] is None and record["state"] == "PENDING"
    )
    import json
    from pathlib import Path

    fixture = json.loads(
        await asyncio.to_thread(Path("contracts/examples/UsageEvent.json").read_text)
    )["success"]
    fixture.update(
        scope=env.scope.model_dump(),
        attempt_id=p.attempt_id,
        connection_id=p.connection_id,
        source_request_id="provider_" + p.attempt_id,
    )
    await env.usage.ledger.settle(UsageEvent.model_validate(fixture))
    record = (await stored(env, "usage_records"))[0]
    assert record["input_tokens"] == 120 and record["cached_tokens"] == 20
    assert record["output_tokens"] is None and record["pricing_status"] == "UNPRICED"
    assert record["state"] == "PENDING"


async def test_query_and_export_cannot_expand_scope_and_conversion_keeps_source(usage_env):
    from creativity_service.modules.usage.schemas import ExchangeRateCreate

    env = usage_env
    await price(env)
    p = await prepare(env)
    await env.usage.ledger.settle(event(env, p))
    await env.usage.management.exchange_rate(
        env.channel.manager,
        ExchangeRateCreate(
            base_currency="USD",
            quote_currency="CNY",
            rate="7.1",
            effective_at=utcnow() - timedelta(days=1),
            source="运营核定",
        ),
    )
    query = UsageFilter(
        start_at=utcnow() - timedelta(days=1),
        end_at=utcnow() + timedelta(minutes=1),
        target_currency="CNY",
    )
    summary = await env.usage.queries.summary(env.scope.channel_id, [env.scope], query)
    assert summary.costs[0].currency == "USD"
    assert summary.costs[0].conversion["amount"] == "4.97000000"
    headers = {"Authorization": "Bearer " + env.channel.token.access_token}
    response = await env.client.get(
        "/admin/v1/usage/summary",
        params=query.model_dump(mode="json", exclude_none=True),
        headers=headers,
    )
    assert response.status_code == 200, response.text
    response = await env.client.get(
        "/admin/v1/usage/records",
        params={
            **query.model_dump(mode="json", exclude_none=True),
            "data_scope_id": "not-authorized",
        },
        headers=headers,
    )
    assert response.status_code == 200, response.text
    assert response.json()["total"] == 0
    response = await env.client.get(
        "/admin/v1/usage/summary",
        params={**query.model_dump(mode="json", exclude_none=True), "channel_id": "other"},
        headers=headers,
    )
    assert response.status_code == 422


async def test_platform_export_is_system_owned_and_business_ledger_stays_in_channel(usage_env):
    env = usage_env
    await price(env)
    p = await prepare(env)
    await env.usage.ledger.settle(event(env, p))
    query = UsageFilter(
        start_at=utcnow() - timedelta(days=1), end_at=utcnow() + timedelta(minutes=1)
    )
    job = await env.usage.exports.create_platform(env.admin, [env.scope.channel_id], query)
    await env.usage.exports.process("system", job.id)
    data = await env.usage.exports.platform_download(env.admin, job.id)
    assert "租号渠道" in data.decode("utf-8-sig")
    async with env.engine.connect() as connection:
        jobs = await rows(connection, "usage_exports", "system")
    assert jobs[0]["channel_range"] == [env.scope.channel_id]
    assert jobs[0]["object_key"].startswith("channels/system/")
    assert len(await stored(env, "usage_records")) == 1
    with pytest.raises(ServiceError):
        await env.usage.exports.platform_download(env.channel.manager, job.id)


async def test_simultaneous_final_callback_and_next_reservation_preserve_exposure(usage_env):
    env = usage_env
    await price(env)
    await budget(env, limit="3")
    first = await prepare(env)
    second = plan()
    # 准入所需额度必须满足；先以较小候选上限受理，再用实际上下文重新预占。
    await admit(env, second.model_copy(update={"input_tokens": 0, "max_output_tokens": 0}))
    actual = event(env, first)
    results = await asyncio.gather(
        env.usage.ledger.settle(actual),
        env.usage.ledger.settle(actual),
        env.usage.budgets.reserve_attempt(env.context, second),
        return_exceptions=True,
    )
    for result in results:
        if isinstance(result, ServiceError):
            assert result.code == "BUDGET_EXCEEDED"
    if any(isinstance(result, ServiceError) for result in results):
        await env.usage.budgets.reserve_attempt(env.context, second)
    assert len(await stored(env, "usage_events")) == 1
    async with transaction(env.engine, env.scope, [ledger_key(env.scope.channel_id)]) as uow:
        policy = (await env.usage.budgets.policies(uow))[0]
        assert await env.usage.budgets.exposure(uow, policy, utcnow()) == Decimal("2.7")


async def test_run_source_cannot_be_rebound_and_out_of_order_delta_is_retained(usage_env):
    env = usage_env
    await price(env)
    p = await prepare(env)
    forged = env.context.model_copy(update={"actor_id": "different", "principal_id": "different"})
    with pytest.raises(ServiceError):
        await admit(env, p, forged)
    first = event(env, p, version=1, final=False, tokens={"input": 10, "output": 5}).model_copy(
        update={"cumulative": False}
    )
    last = event(env, p, version=2, final=True, tokens={"input": 20, "output": 10}).model_copy(
        update={"cumulative": False}
    )
    await env.usage.ledger.settle(last)
    updated = await env.usage.ledger.settle(first)
    assert updated["input_tokens"] == 30 and updated["output_tokens"] == 15
    assert updated["amount"] == Decimal("0.45") and updated["state"] == "SETTLED"


async def test_actual_key_rotation_late_usage_remains_on_original_key(usage_env):
    from creativity_service.modules.channels.schemas import KeyRotate, TokenExchange

    env = usage_env
    key = await credential(env, env.channel)
    context = key.context.model_copy(update={"scope": env.scope})
    first = await prepare(env, context=context)
    rotated = await env.services.keys.rotate(
        env.channel.manager,
        env.scope.channel_id,
        key.key.key.key_id,
        KeyRotate(
            revision=key.key.key.revision,
            expires_at=utcnow() + timedelta(hours=1),
            overlap_seconds=0,
        ),
    )
    token = await env.services.keys.exchange(
        TokenExchange(api_key=rotated.api_key), new_id("request")
    )
    replacement = await env.iam.authentication.authenticate(token.access_token, "service")
    second = await prepare(env, context=replacement.model_copy(update={"scope": env.scope}))
    await env.usage.ledger.settle(event(env, first))
    await env.usage.ledger.settle(event(env, second))
    records = await stored(env, "usage_records")
    assert {r["key_id"] for r in records} == {context.key_id, replacement.key_id}
    assert {r["client_id"] for r in records} == {context.client_id}


async def test_admission_and_reservation_share_caller_transaction(usage_env):
    env = usage_env
    await price(env)
    await budget(env)
    p = plan()
    with pytest.raises(ServiceError):
        async with transaction(
            env.engine, env.scope, env.usage.budgets.admission_keys(env.context, p.run_id)
        ) as uow:
            await env.usage.budgets.admit(uow, env.context, p.run_id, p)
            await env.usage.budgets.reserve_attempt(
                env.context, p.model_copy(update={"input_tokens": 500}), uow=uow
            )
    assert not await stored(env, "admissions")
    assert not await stored(env, "usage_records")


async def test_model_concurrency_checks_actual_fallback_model(usage_env):
    env = usage_env
    parent = await budget(env, unit="concurrency", limit="3")
    async with transaction(
        env.engine,
        env.scope,
        [ledger_key(env.scope.channel_id), budget_configuration_key(env.scope.channel_id)],
    ) as uow:
        await save(
            uow,
            "budget_policies",
            new_id("budget"),
            {
                **{k: getattr(parent, k) for k in BudgetCreate.model_fields},
                "name": "回退模型并发",
                "scope_type": "model",
                "scope_id": "fallback",
                "limit_value": Decimal(1),
                "version_id": parent.version_id,
                "thresholds": ["0.8", "1"],
            },
        )
    first = await prepare(env, plan().model_copy(update={"model_id": "fallback"}))
    next_plan = plan()
    await admit(env, next_plan)
    with pytest.raises(ServiceError) as rejected:
        await env.usage.budgets.reserve_attempt(
            env.context, next_plan.model_copy(update={"model_id": "fallback"})
        )
    assert rejected.value.code == "BUDGET_EXCEEDED"
    await env.usage.ledger.settle(event(env, first))
    await env.usage.budgets.reserve_attempt(
        env.context, next_plan.model_copy(update={"model_id": "fallback"})
    )


async def test_model_price_port_rejects_incomplete_price_dimensions(usage_env):
    from types import SimpleNamespace

    env = usage_env
    await price(env)
    candidate = SimpleNamespace(
        scope=env.scope, model_id="model_demo", parameters={"max_tokens": 100}
    )
    async with transaction(
        env.engine,
        env.scope,
        [ledger_key(env.scope.channel_id), budget_configuration_key(env.scope.channel_id)],
    ) as uow:
        await env.usage.prices.require_priced(uow, env.context, [candidate])
        await save(
            uow,
            "price_versions",
            new_id("price"),
            {
                "name": "缺少输出维度",
                "model_id": "model_demo",
                "currency": "USD",
                "price_items": [{"dimension": "input", "amount": "1", "per_units": 100}],
                "subset_relations": {},
                "unit": "每百 Token",
                "effective_at": utcnow() - timedelta(seconds=1),
                "source": "价格门禁夹具",
            },
        )
        with pytest.raises(ServiceError) as rejected:
            await env.usage.prices.require_priced(uow, env.context, [candidate])
        assert rejected.value.code == "BUDGET_PRICE_REQUIRED"


async def test_local_request_id_binds_once_and_replays_without_double_charge(usage_env):
    env = usage_env
    await price(env)
    await budget(env, limit="10")
    p = await prepare(env)
    missing = event(env, p, status="MISSING", final=False).model_copy(
        update={"source_request_id": p.attempt_id, "raw_usage": None}
    )
    await env.usage.ledger.settle(missing)
    await env.usage.ledger.finish_attempt(env.scope, p.attempt_id, "SUCCEEDED")
    reported = event(env, p, version=2)
    results = await asyncio.gather(*(env.usage.ledger.settle(reported) for _ in range(3)))
    assert {r["state"] for r in results} == {"SETTLED"}
    replay = await env.usage.ledger.settle(missing)
    assert replay["source_request_id"] == reported.source_request_id
    assert replay["amount"] == Decimal("0.7")
    assert replay["outcome"] == "SUCCEEDED"
    assert len(await stored(env, "usage_events")) == 2
    assert len(await stored(env, "usage_adjustments")) == 2
    with pytest.raises(ServiceError) as changed:
        await env.usage.ledger.settle(reported.model_copy(update={"source_request_id": "another"}))
    assert changed.value.code == "SCOPE_MISMATCH"
    other = await prepare(env)
    with pytest.raises(ServiceError) as reused:
        await env.usage.ledger.settle(
            event(env, other, version=3).model_copy(
                update={"source_request_id": reported.source_request_id}
            )
        )
    assert reused.value.code == "USAGE_EVENT_CONFLICT"
    # 恢复扫描的未知结果与重复迟报都不能把已确认的成功改回进行中或未知。
    await env.usage.ledger.finish_attempt(env.scope, p.attempt_id, "UNKNOWN")
    assert (await stored(env, "usage_records", attempt_id=p.attempt_id))[0][
        "outcome"
    ] == "SUCCEEDED"


async def test_confirmed_unsent_releases_hard_budget_without_treating_unknown_as_free(usage_env):
    env = usage_env
    await price(env)
    await budget(env)
    p = await prepare(env)
    missing = event(env, p, status="MISSING", final=False).model_copy(
        update={"source_request_id": p.attempt_id, "raw_usage": None}
    )
    await env.usage.ledger.settle(missing)
    assert await env.usage.ledger.release_unused(env.scope, p.attempt_id) == "PENDING"
    released = await env.usage.ledger.finish_attempt(
        env.scope, p.attempt_id, "FAILED", confirmed_unsent=True
    )
    assert released["state"] == "RELEASED"
    (reservation,) = await stored(env, "budget_reservations", attempt_id=p.attempt_id)
    assert reservation["status"] == "RELEASED" and reservation["settled_amount"] == 0
    # 同一硬额度可立即容纳下一次预占；无发送证明的下一次尝试仍占用额度。
    next_attempt = await prepare(env)
    assert await env.usage.ledger.release_unused(env.scope, next_attempt.attempt_id) == "PENDING"
    with pytest.raises(ServiceError) as blocked:
        await prepare(env)
    assert blocked.value.code == "BUDGET_EXCEEDED"
