"""真实运行、IAM、数据库验证三层记忆；仅模型返回值使用受控夹具。"""

import asyncio
from datetime import timedelta

import pytest
from sqlalchemy import update

from creativity_service.core.context import TaskEnvelope
from creativity_service.core.database import transaction
from creativity_service.core.deletion import RecoveryService
from creativity_service.core.primitives import ServiceError, new_id, utcnow
from creativity_service.modules.budgets.schemas import BudgetCreate, PlatformLimitCreate
from creativity_service.modules.conversations.schemas import ConversationCreate, MessageInput
from creativity_service.modules.conversations.tables import metadata as conversations
from creativity_service.modules.memory import repositories as repo
from creativity_service.modules.memory.schemas import (
    MemoryConfirm,
    MemoryPolicy,
    PreferenceInput,
)
from creativity_service.modules.runs import repositories as run_repo
from creativity_service.modules.usage.assembly import build_usage_services
from creativity_service.workers.executor import execute_message
from tests.integration.agents.test_agents import publish
from tests.integration.runtime.conftest import agent_env, channel_env, runtime_env

__all__ = ["runtime_env", "agent_env", "channel_env"]
pytestmark = [
    pytest.mark.integration,
    pytest.mark.parametrize(
        "agent_env",
        [
            {
                "environment": "test",
                "independent_actions": ["release:publish", "data:read_sensitive", "data:export"],
            }
        ],
        indirect=True,
    ),
]


async def setup(env):
    definition = env.definition.model_copy(
        update={
            "context": env.definition.context.model_copy(
                update={
                    "conversation_enabled": True,
                    "summary_policy": "recent",
                    "memory_policy": MemoryPolicy(),
                }
            )
        }
    )
    detail = await env.agents.create(
        env.context, env.body.model_copy(update={"definition": definition})
    )
    await publish(env, detail)
    env.context = env.context.model_copy(
        update={
            "scope": env.context.scope.model_copy(
                update={"subject_type": "user", "subject_id": "person"}
            )
        }
    )
    await RecoveryService(env.engine, env.iam.authorization).initialize_fresh(env.context)
    conversation = await env.conversations.create(
        env.context, ConversationCreate(agent_code=env.body.agent_code, title="语言与行程")
    )
    receipt = await env.conversations.submit(
        env.context,
        conversation.conversation_id,
        MessageInput(
            client_message_id="one",
            content="我长期习惯使用中文。这次只安排一天。",
            input={"request": "请处理"},
        ),
    )
    await execute_message(
        env.runs,
        TaskEnvelope(channel_id=env.context.scope.channel_id, run_id=receipt.run.run_id),
        "conversation",
        env.runtime,
    )
    result = await env.runs.get_run(env.context, receipt.run.run_id)
    assert result.state == "SUCCEEDED", result.error
    env.cid = conversation.conversation_id
    async with env.engine.begin() as connection:
        await connection.execute(
            update(conversations.tables["conversations"])
            .where(conversations.tables["conversations"].c.id == env.cid)
            .values(updated_at=utcnow() - timedelta(hours=1))
        )
    return env.runs.memory_consolidation


async def jobs(env):
    async with env.engine.connect() as connection:
        return await repo.rows(connection, "memory_consolidations", env.context.scope)


async def generate(env, service):
    env.adapter.responses = [
        {"summary": "用户长期使用中文；本次行程仅一天，属于单次条件。"},
        {"profiles": [{"key": "preferred_language", "value": "中文"}]},
    ]
    await service.sweep(env.context.scope.channel_id)
    job = (await jobs(env))[0]
    assert job["state"] == "ADMITTED", job
    await execute_message(
        env.runs,
        TaskEnvelope(channel_id=env.context.scope.channel_id, run_id=job["generation_run_id"]),
        "memory",
        env.runtime,
    )
    result = await env.runs.get_run(env.context, job["generation_run_id"])
    assert result.state == "SUCCEEDED", result.error
    return (await jobs(env))[0]


async def test_background_generates_layers_and_next_conversation_reads_confirmed_profile(
    runtime_env,
):
    env = runtime_env
    service = await setup(env)
    assert not (await env.memory.list_memories(env.context)).items
    job = await generate(env, service)
    assert job["state"] == "COMPLETED"
    all_memories = (await env.memory.list_memories(env.context)).items
    archive = next(m for m in all_memories if m.layer == "archive")
    profile = next(m for m in all_memories if m.layer == "profile")
    assert archive.status == "ACTIVE" and profile.status == "PROPOSED"
    assert len(archive.sources) == 2
    assert [source.name for source in profile.sources] == ["会话归档"]
    assert len(env.adapter.calls) == 3
    await asyncio.gather(
        service.sweep(env.context.scope.channel_id), service.sweep(env.context.scope.channel_id)
    )
    assert len(await jobs(env)) == 1
    await env.memory.confirm(
        env.context, profile.memory_id, MemoryConfirm(revision=profile.revision)
    )
    next_conversation = await env.conversations.create(
        env.context, ConversationCreate(agent_code=env.body.agent_code, title="新的会话")
    )
    receipt = await env.conversations.submit(
        env.context,
        next_conversation.conversation_id,
        MessageInput(client_message_id="next", content="你好", input={"request": "请处理"}),
    )
    await execute_message(
        env.runs,
        TaskEnvelope(channel_id=env.context.scope.channel_id, run_id=receipt.run.run_id),
        "next",
        env.runtime,
    )
    request = env.adapter.calls[-1][1]
    assert '"preferred_language":"中文"' in request.messages[-1]["content"]
    assert '"archives":[' in request.messages[-1]["content"]
    assert "本次行程仅一天" in request.messages[-1]["content"]
    await env.memory.forget(env.context, archive.memory_id)
    with pytest.raises(ServiceError) as blocked:
        await env.runs.get_run(env.context, job["generation_run_id"])
    assert blocked.value.code == "CONTENT_DELETED"
    assert (await env.memory.detail(env.context, profile.memory_id)).memory.status == "ACTIVE"


async def test_late_model_result_cannot_write_after_memory_disabled(runtime_env):
    env = runtime_env
    service = await setup(env)
    env.adapter.responses = [{"summary": "用户长期使用中文。"}]
    await service.sweep(env.context.scope.channel_id)
    job = (await jobs(env))[0]

    async def disable(context, attempt):
        await env.memory.set_preferences(env.context, PreferenceInput(enabled=False, revision=0))

    env.adapter.before = disable
    await execute_message(
        env.runs,
        TaskEnvelope(channel_id=env.context.scope.channel_id, run_id=job["generation_run_id"]),
        "memory",
        env.runtime,
    )
    assert not (await env.memory.list_memories(env.context)).items
    await service.sweep(env.context.scope.channel_id)
    assert (await jobs(env))[0]["state"] == "SKIPPED"


async def test_removing_one_message_revokes_whole_archive_and_unconfirmed_profile(runtime_env):
    from creativity_service.core.deletion import ContentRef, DeletionService
    from tests.integration.core.conftest import TestAuthorization

    env = runtime_env
    service = await setup(env)
    job = await generate(env, service)
    await DeletionService(env.engine, TestAuthorization()).mark(
        env.context, ContentRef("message", job["source_message_ids"][-1]), "SOURCE_DELETED"
    )
    for identifier in job["memory_ids"]:
        detail = await env.memory.detail(env.context, identifier)
        assert detail.memory.status == "REVOKED" and detail.memory.value is None
    await service.sweep(env.context.scope.channel_id)
    assert len(await jobs(env)) == 1


@pytest.mark.parametrize("interruption", ["clear", "source", "permission"])
async def test_pending_generation_rechecks_clear_source_and_permission(runtime_env, interruption):
    from creativity_service.core.deletion import ContentRef, DeletionService
    from creativity_service.core.primitives import ServiceError
    from tests.integration.core.conftest import TestAuthorization

    env = runtime_env
    service = await setup(env)
    env.adapter.responses = [
        {"summary": "用户长期使用中文。"},
        {"profiles": [{"key": "preferred_language", "value": "中文"}]},
    ]
    await service.sweep(env.context.scope.channel_id)
    job = (await jobs(env))[0]
    original = env.memory.authorization.require

    async def deny(context, action, resource_id):
        if action == "memory:write":
            raise ServiceError("FORBIDDEN", "记忆授权已撤销", 403)
        await original(context, action, resource_id)

    async def interrupt(context, attempt):
        if interruption == "clear":
            await env.memory.clear(env.context)
        elif interruption == "source":
            await DeletionService(env.engine, TestAuthorization()).mark(
                env.context, ContentRef("message", job["source_message_ids"][0]), "SOURCE_DELETED"
            )
        else:
            env.memory.authorization.require = deny

    env.adapter.before = interrupt
    try:
        await execute_message(
            env.runs,
            TaskEnvelope(channel_id=env.context.scope.channel_id, run_id=job["generation_run_id"]),
            "late",
            env.runtime,
        )
        assert not (await env.memory.list_memories(env.context)).items
    finally:
        env.memory.authorization.require = original
    if interruption == "clear":
        env.adapter.before, env.adapter.responses = None, []
        receipt = await env.conversations.submit(
            env.context,
            env.cid,
            MessageInput(
                client_message_id="after_clear", content="新的消息", input={"request": "请处理"}
            ),
        )
        await execute_message(
            env.runs,
            TaskEnvelope(channel_id=env.context.scope.channel_id, run_id=receipt.run.run_id),
            "new_message",
            env.runtime,
        )
        async with env.engine.begin() as connection:
            await connection.execute(
                update(conversations.tables["conversations"])
                .where(conversations.tables["conversations"].c.id == env.cid)
                .values(updated_at=utcnow() - timedelta(hours=1))
            )
        await service.sweep(env.context.scope.channel_id)
        await service.sweep(env.context.scope.channel_id)
        pending = [j for j in await jobs(env) if j["state"] == "ADMITTED"]
        assert len(pending) == 1
        assert not set(pending[0]["source_message_ids"]) & set(job["source_message_ids"])


async def test_failed_background_run_retries_with_same_batch_and_no_duplicate_memories(runtime_env):
    env = runtime_env
    service = await setup(env)
    await asyncio.gather(
        service.sweep(env.context.scope.channel_id), service.sweep(env.context.scope.channel_id)
    )
    job = (await jobs(env))[0]
    env.adapter.failures = ["MODEL_UNAVAILABLE"]
    await execute_message(
        env.runs,
        TaskEnvelope(channel_id=env.context.scope.channel_id, run_id=job["generation_run_id"]),
        "failure",
        env.runtime,
    )
    await service.sweep(env.context.scope.channel_id)
    retry = (await jobs(env))[0]
    assert retry["state"] == "PENDING" and retry["attempt"] == 2
    async with transaction(env.engine, env.context.scope, repo.keys(env.context.scope)) as uow:
        await repo.save(
            uow,
            "memory_consolidations",
            job["id"],
            {"next_attempt_at": utcnow() - timedelta(seconds=1)},
        )
    completed = await generate(env, service)
    assert (
        completed["id"] == job["id"] and completed["generation_run_id"] != job["generation_run_id"]
    )
    assert len((await env.memory.list_memories(env.context)).items) == 2


@pytest.mark.parametrize("interruption", ["cancel", "deadline", "lease_expired", "lease_replaced"])
async def test_generation_rechecks_run_control_before_memory_commit(
    runtime_env, monkeypatch, interruption
):
    env = runtime_env
    service = await setup(env)
    env.adapter.responses = [
        {"summary": "用户长期使用中文。"},
        {"profiles": [{"key": "preferred_language", "value": "中文"}]},
    ]
    await service.sweep(env.context.scope.channel_id)
    job = (await jobs(env))[0]
    message = TaskEnvelope(channel_id=env.context.scope.channel_id, run_id=job["generation_run_id"])
    original = service.authorize
    boundary_calls = 0
    replacement = None

    async def interrupt(context, conversation_id, source_run_id):
        nonlocal boundary_calls, replacement
        await original(context, conversation_id, source_run_id)
        boundary_calls += 1
        if boundary_calls != 3:
            return
        if interruption == "cancel":
            await env.runs.cancel(env.context, message.run_id)
            return
        async with transaction(
            env.engine, env.context.scope, env.runs.keys(env.context, message.run_id)
        ) as uow:
            if interruption == "deadline":
                await run_repo.save(
                    uow, "runs", message.run_id, {"deadline": utcnow() - timedelta(seconds=1)}
                )
            else:
                lease = await run_repo.required(
                    uow.connection, "run_leases", message.channel_id, run_id=message.run_id
                )
                await run_repo.save(
                    uow, "run_leases", lease["id"], {"expires_at": utcnow() - timedelta(seconds=1)}
                )
        if interruption == "lease_replaced":
            replacement = await env.runs.claim_lease(message, "replacement")
            assert replacement is not None

    monkeypatch.setattr(service, "authorize", interrupt)
    await execute_message(env.runs, message, "memory", env.runtime)
    assert boundary_calls == 3
    assert not (await env.memory.list_memories(env.context)).items
    assert (await jobs(env))[0]["state"] != "COMPLETED"
    monkeypatch.setattr(service, "authorize", original)
    if interruption == "cancel":
        assert (await env.runs.load(message))["state"] == "CANCELLED"
        await service.sweep(message.channel_id)
        stopped = (await jobs(env))[0]
        assert stopped["state"] == "SKIPPED"
        assert stopped["error_code"] == "RUN_CANCELLED"
        assert stopped["attempt"] == job["attempt"]
        await service.sweep(message.channel_id)
        assert (await jobs(env))[0] == stopped
    elif interruption == "deadline":
        assert (await env.runs.load(message))["state"] == "TIMED_OUT"
    else:
        # 新租约从已保存的模型结果恢复，旧执行器不能提前写回或重复调用。
        if replacement:
            await env.runtime.execute(env.context, replacement)
        else:
            await execute_message(env.runs, message, "recovered", env.runtime)
        assert (await env.runs.load(message))["state"] == "SUCCEEDED"
        assert (await jobs(env))[0]["state"] == "COMPLETED"
        assert len((await env.memory.list_memories(env.context)).items) == 2
        assert len(env.adapter.calls) == 3


async def test_memory_write_and_run_success_rollback_together(runtime_env, monkeypatch):
    env = runtime_env
    service = await setup(env)
    env.adapter.responses = [
        {"summary": "用户长期使用中文。"},
        {"profiles": [{"key": "preferred_language", "value": "中文"}]},
    ]
    await service.sweep(env.context.scope.channel_id)
    job = (await jobs(env))[0]
    original = env.runs.transition

    async def fail_success(uow, row, state, **kwargs):
        if state == "SUCCEEDED":
            raise ServiceError("TEST_COMMIT_FAILED", "模拟成功终态保存失败", 503)
        return await original(uow, row, state, **kwargs)

    monkeypatch.setattr(env.runs, "transition", fail_success)
    message = TaskEnvelope(channel_id=env.context.scope.channel_id, run_id=job["generation_run_id"])
    await execute_message(env.runs, message, "memory", env.runtime)
    assert (await env.runs.load(message))["state"] == "FAILED"
    assert (await jobs(env))[0]["state"] == "ADMITTED"
    assert not (await env.memory.list_memories(env.context)).items
    events = await env.runs.events(env.context, message.run_id)
    assert [e.event_type for e in events].count("completed") == 1
    assert "result" not in [e.event_type for e in events]


async def test_memory_success_is_committed_before_later_cancel(runtime_env, monkeypatch):
    env = runtime_env
    service = await setup(env)
    original = env.runs.after_commit
    completed = []

    async def cancel_after_commit(row):
        if row["state"] == "SUCCEEDED" and not completed:
            completed.append(row["id"])
            assert (await jobs(env))[0]["state"] == "COMPLETED"
            assert len((await env.memory.list_memories(env.context)).items) == 2
            assert (await env.runs.cancel(env.context, row["id"])).state == "SUCCEEDED"
        await original(row)

    monkeypatch.setattr(env.runs, "after_commit", cancel_after_commit)
    job = await generate(env, service)
    assert completed == [job["generation_run_id"]]
    events = await env.runs.events(env.context, job["generation_run_id"])
    assert [e.event_type for e in events].count("completed") == 1
    assert [e.event_type for e in events].count("result") == 1
    run = await env.runs.load(
        TaskEnvelope(channel_id=env.context.scope.channel_id, run_id=job["generation_run_id"])
    )
    assert run["resources_released"]


@pytest.mark.parametrize("limit_scope", ["platform", "channel"])
async def test_transient_admission_limit_retries_same_batch_after_capacity_returns(
    runtime_env, limit_scope
):
    env = runtime_env
    service = await setup(env)
    usage = build_usage_services(env.engine, env.services.channels)
    if limit_scope == "platform":
        await usage.management.platform_limits(
            env.admin,
            PlatformLimitCreate(
                limit_code="parallel", name="平台并发", unit="concurrency", limit_value=1
            ),
        )
    else:
        await usage.management.save_budget(
            env.tenant.manager,
            BudgetCreate(
                name="渠道并发",
                scope_type="channel",
                scope_id=env.context.scope.channel_id,
                unit="concurrency",
                limit_value="1",
            ),
        )
    blocker = new_id("run")
    async with transaction(
        env.engine, env.context.scope, env.runs.budgets.admission_keys(env.context, blocker)
    ) as uow:
        await env.runs.budgets.admit(uow, env.context, blocker)
    await service.sweep(env.context.scope.channel_id)
    job = (await jobs(env))[0]
    assert job["state"] == "PENDING"
    assert job["error_code"] == (
        "PLATFORM_LIMIT_EXCEEDED" if limit_scope == "platform" else "BUDGET_EXCEEDED"
    )
    assert job["attempt"] == 1 and job["generation_run_id"] is None
    assert job["next_attempt_at"] > utcnow()
    await env.runs.budgets.finish_admission(env.context, blocker)
    await service.sweep(env.context.scope.channel_id)
    assert (await jobs(env))[0] == job
    async with transaction(env.engine, env.context.scope, repo.keys(env.context.scope)) as uow:
        await repo.save(
            uow,
            "memory_consolidations",
            job["id"],
            {"next_attempt_at": utcnow() - timedelta(seconds=1)},
        )
    await asyncio.gather(
        service.sweep(env.context.scope.channel_id), service.sweep(env.context.scope.channel_id)
    )
    admitted = (await jobs(env))[0]
    assert admitted["state"] == "ADMITTED" and admitted["error_code"] is None
    completed = await generate(env, service)
    assert completed["id"] == job["id"] and completed["attempt"] == 1
    assert completed["source_message_ids"] == job["source_message_ids"]
    assert len(await jobs(env)) == 1
    assert len((await env.memory.list_memories(env.context)).items) == 2


async def test_late_admission_error_cannot_overwrite_concurrent_success(runtime_env, monkeypatch):
    env = runtime_env
    service = await setup(env)
    original = service.admission.submit
    waiting, resume = asyncio.Event(), asyncio.Event()

    async def delayed_limit(*args, **kwargs):
        if not waiting.is_set():
            waiting.set()
            await resume.wait()
            raise ServiceError("PLATFORM_LIMIT_EXCEEDED", "平台并发暂满", 429)
        return await original(*args, **kwargs)

    monkeypatch.setattr(service.admission, "submit", delayed_limit)
    previous = asyncio.create_task(service.sweep(env.context.scope.channel_id))
    try:
        await asyncio.wait_for(waiting.wait(), timeout=15)
        await service.sweep(env.context.scope.channel_id)
        admitted = (await jobs(env))[0]
        assert admitted["state"] == "ADMITTED" and admitted["error_code"] is None
    finally:
        resume.set()
        await asyncio.wait_for(previous, timeout=15)
    assert (await jobs(env))[0] == admitted
    async with env.engine.connect() as connection:
        runs = await run_repo.rows(connection, "runs", env.context.scope.channel_id)
    assert len(runs) == 2


async def test_discovery_batches_history_and_keeps_whole_turns(runtime_env):
    from sqlalchemy import insert, select

    from creativity_service.modules.memory.schemas import ConsolidationSettings, PolicyInput
    from creativity_service.modules.runs.tables import metadata as runs
    from tests.integration.agents.test_read_queries import statements

    env = runtime_env
    service = await setup(env)
    policy = await env.memory.get_policy(env.context)
    await env.memory.set_policy(
        env.context,
        PolicyInput(
            revision=policy.revision,
            consolidation=ConsolidationSettings(batch_messages=3),
        ),
    )
    async with env.engine.connect() as connection:
        conversation = dict(
            (
                await connection.execute(
                    select(conversations.tables["conversations"]).where(
                        conversations.tables["conversations"].c.id == env.cid
                    )
                )
            )
            .mappings()
            .one()
        )
        original_messages = [
            dict(row)
            for row in (
                await connection.execute(
                    select(conversations.tables["messages"])
                    .where(conversations.tables["messages"].c.conversation_id == env.cid)
                    .order_by(conversations.tables["messages"].c.sequence)
                )
            ).mappings()
        ]
        run = await run_repo.required(
            connection, "runs", env.context.scope.channel_id, id=original_messages[0]["run_id"]
        )
        content = await run_repo.required(
            connection,
            "run_contents",
            env.context.scope.channel_id,
            run_id=run["id"],
            kind="execution_spec",
        )
    with statements(env.engine) as single:
        await service.discover(conversation)
    first = (await jobs(env))[0]
    async with transaction(env.engine, env.context.scope, repo.keys(env.context.scope)) as uow:
        await repo.save(uow, "memory_consolidations", first["id"], {"state": "COMPLETED"})
    # 旧批次已消费；加入 120 个完整轮次，扫描仍只装载下一批及关联冻结定义。
    async with env.engine.begin() as connection:
        await connection.execute(
            insert(runs.tables["runs"]),
            [
                {
                    **run,
                    "id": f"batch_run_{i:03}",
                    "created_at": run["created_at"] + timedelta(microseconds=i + 1),
                }
                for i in range(120)
            ],
        )
        await connection.execute(
            insert(runs.tables["run_contents"]),
            [
                {**content, "id": f"batch_spec_{i:03}", "run_id": f"batch_run_{i:03}"}
                for i in range(120)
            ],
        )
        await connection.execute(
            insert(conversations.tables["messages"]),
            [
                {
                    **message,
                    "id": f"batch_message_{i:03}_{offset}",
                    "run_id": f"batch_run_{i:03}",
                    "turn_id": f"batch_turn_{i:03}",
                    "sequence": 3 + i * 2 + offset,
                }
                for i in range(120)
                for offset, message in enumerate(original_messages)
            ],
        )
    with statements(env.engine) as multiple:
        await service.discover(conversation)
    second = next(row for row in await jobs(env) if row["id"] != first["id"])
    assert second["source_message_ids"] == [
        f"batch_message_{i:03}_{offset}" for i in range(2) for offset in range(2)
    ]
    assert len(multiple) <= len(single) + 2
    message_queries = [q for q in multiple if "FROM messages" in q]
    assert all("LIMIT" in q or " IN (" in q for q in message_queries)
    async with transaction(env.engine, env.context.scope, repo.keys(env.context.scope)) as uow:
        await repo.save(uow, "memory_consolidations", second["id"], {"state": "COMPLETED"})
    await service.discover(conversation)
    third = next(row for row in await jobs(env) if row["id"] not in {first["id"], second["id"]})
    assert third["source_message_ids"] == [
        f"batch_message_{i:03}_{offset}" for i in range(2, 4) for offset in range(2)
    ]
