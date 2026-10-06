"""SES-A01—A06：事务并发、故障投影、版本与删除屏障。"""

import asyncio

import pytest

from creativity_service.core.context import Scope, TaskEnvelope
from creativity_service.core.contracts import BusinessResult
from creativity_service.core.database import transaction
from creativity_service.core.deletion import ContentRef, DeletionService, RecoveryService
from creativity_service.core.primitives import RunInput, ServiceError
from creativity_service.modules.conversations import repositories as repo
from creativity_service.modules.conversations.schemas import (
    AttachmentPart,
    ConversationCreate,
    MessageInput,
    TitleInput,
)
from creativity_service.modules.runs.schemas import RunRequest
from creativity_service.workers.recovery import Recovery
from tests.integration.runs.test_runs import values

pytestmark = pytest.mark.integration


def message(key="one", value=1, content="请帮我处理", **kwargs):
    return MessageInput(client_message_id=key, content=content, input={"value": value}, **kwargs)


async def submit(env, key="one", **kwargs):
    return await env.conversations.submit(env.context, env.cid, message(key, **kwargs))


async def finish(env, receipt, business_status="COMPLETED", data=None):
    lease = await env.runs.claim_lease(
        TaskEnvelope(channel_id=env.context.scope.channel_id, run_id=receipt.run.run_id), "worker"
    )
    assert lease
    await env.runs.append_event(lease, "text_delta", {"text": "已处理"})
    result = BusinessResult(
        schema_version="1",
        business_status=business_status,
        data=data or {"value": 1},
        warnings=(),
        evidence_refs=(),
    )
    await env.runs.finish_run(lease, "SUCCEEDED", result)
    return lease


async def records(env, name, **filters):
    async with env.engine.connect() as connection:
        return await repo.rows(connection, name, env.context.scope, **filters)


async def publish_version(env, label, output_schema=None):
    definition = env.resolver.definition
    output_schema = output_schema or definition.output_schema
    agent = await env.runs.versions.create_draft(
        env.context,
        "agent",
        "agent_one",
        label,
        {},
        list(definition.version_ids[1:]),
        output_schema,
    )
    agent = await env.runs.versions.freeze(env.context, agent.version_id, 1)
    policy = definition.policy.model_copy(
        update={
            "steps": tuple(
                s.model_copy(update={"target_version_id": agent.version_id})
                if s.target_version_id == definition.version_ids[0]
                else s
                for s in definition.policy.steps
            )
        }
    )
    env.resolver.definition = definition.model_copy(
        update={
            "version_ids": (agent.version_id, *definition.version_ids[1:]),
            "output_schema": output_schema,
            "policy": policy,
        }
    )


async def test_ses_a01_concurrent_different_messages_only_one_run(env):
    results = await asyncio.gather(*(submit(env, str(i)) for i in range(6)), return_exceptions=True)
    assert sum(not isinstance(r, Exception) for r in results) == 1
    assert all(r.code == "SESSION_BUSY" for r in results if isinstance(r, ServiceError))
    assert len(await records(env, "messages")) == 2
    assert len(await records(env, "conversation_turns")) == len(await values(env, "runs")) == 1


async def test_ses_a02_replay_across_keys_and_release_changes(env):
    receipts = await asyncio.gather(*(submit(env) for _ in range(6)))
    assert len({r.run.run_id for r in receipts}) == 1
    env.runs.resolver = None
    assert (await submit(env)).run.run_id == receipts[0].run.run_id
    with pytest.raises(ServiceError, match="正文或输入不同"):
        await submit(env, content="不同正文")
    await env.conversations.archive(env.context, env.cid)
    assert (await submit(env)).run.run_id == receipts[0].run.run_id
    with pytest.raises(ServiceError, match="已归档"):
        await submit(env, "second")


async def test_run_entry_uses_message_id_before_http_key(env):
    request = RunRequest(
        agent_code="test_agent",
        input={"value": 1, "message": "你好"},
        conversation_id=env.cid,
        client_message_id="direct",
    )
    env.resolver.definition = env.resolver.definition.model_copy(
        update={"input_schema": {"type": "object"}}
    )
    async with transaction(
        env.engine, env.context.scope, env.conversations.hooks.keys(env.context, env.cid)
    ) as uow:
        await repo.save(uow, "conversations", env.cid, {"input_schema": {"type": "object"}})
    results = await asyncio.gather(
        *(env.runs.admit_run(env.context, request, f"http_{i}") for i in range(5))
    )
    assert len({r.run_id for r in results}) == 1
    with pytest.raises(ServiceError, match="其他智能体"):
        await env.runs.admit_run(
            env.context, request.model_copy(update={"agent_code": "other"}), "other"
        )
    with pytest.raises(ServiceError, match="消息标识"):
        await env.runs.admit_run(
            env.context,
            RunInput(agent_code="test_agent", input={}, conversation_id=env.cid),
            "missing",
        )


async def test_budget_rejection_rolls_back_message_and_occupancy(env, monkeypatch):
    async def reject(*args, **kwargs):
        raise ServiceError("BUDGET_EXCEEDED", "预算不足", 429)

    monkeypatch.setattr(env.budgets, "admit", reject)
    with pytest.raises(ServiceError, match="预算不足"):
        await submit(env)
    for table in ("messages", "conversation_turns"):
        assert await records(env, table) == []
    for table in ("runs", "run_idempotency", "run_occupancies", "dispatch_outbox"):
        assert await values(env, table) == []
    assert (await env.conversations.detail(env.context, env.cid)).conversation.active_run_id is None


async def test_ses_a03_partial_cancel_and_late_output(env):
    receipt = await submit(env)
    lease = await env.runs.claim_lease(
        TaskEnvelope(channel_id=env.context.scope.channel_id, run_id=receipt.run.run_id), "worker"
    )
    await env.runs.append_event(lease, "text_delta", {"text": "部分内容"})
    await env.runs.append_event(lease, "tool_status", {"role": "tool", "text": "正在查询"})
    page = await env.conversations.messages(env.context, env.cid)
    assert page.items[1].text == "部分内容" and page.items[1].status == "PARTIAL"
    assert page.items[2].role_label == "工具"
    await env.conversations.cancel(env.context, env.cid, receipt.run.run_id)
    await env.runs.finish_run(
        lease,
        "SUCCEEDED",
        BusinessResult(
            schema_version="1",
            business_status="COMPLETED",
            data={"value": 1},
            warnings=(),
            evidence_refs=(),
        ),
    )
    assert await env.runs.append_event(lease, "text_delta", {"text": "迟到内容"}) is None
    page = await env.conversations.messages(env.context, env.cid)
    assert all(m.status == "CANCELLED" for m in page.items)
    assert page.items[1].text == "部分内容" and page.turns[0].result is None


async def test_ses_a04_archive_restore_and_terminal_compensation(env):
    first = await submit(env)

    def crash(point):
        if point == "terminal_committed_before_release":
            raise RuntimeError("终态提交后退出")

    env.runs.fault = crash
    with pytest.raises(RuntimeError):
        await finish(env, first)
    page = await env.conversations.messages(env.context, env.cid)
    assert page.items[1].status == "COMPLETED"
    env.runs.fault = lambda _: None
    await Recovery(env.runs).scan(env.context.scope.channel_id)
    await env.conversations.archive(env.context, env.cid)
    with pytest.raises(ServiceError, match="已归档"):
        await submit(env, "two")
    await env.conversations.archive(env.context, env.cid, restore=True)
    second = await submit(env, "two")
    assert second.sequence == 2
    assert [m.sequence for m in (await env.conversations.messages(env.context, env.cid)).items] == [
        1,
        2,
        3,
        4,
    ]


async def test_ses_a06_versions_and_contract_change(env):
    first = await submit(env)
    await finish(env, first)
    await publish_version(env, "第二版本")
    second = await submit(env, "two")
    await finish(env, second)
    page = await env.conversations.messages(env.context, env.cid)
    assert [t.version_label for t in page.turns] == ["测试初版", "第二版本"]
    env.resolver.definition = env.resolver.definition.model_copy(
        update={"input_schema": {"type": "object", "required": ["extra"]}}
    )
    with pytest.raises(ServiceError) as error:
        await submit(env, "three")
    assert error.value.code == "SESSION_CONTRACT_INCOMPATIBLE"
    assert len(await records(env, "conversation_turns")) == 2


async def test_followup_creates_new_run_and_records_confirmed_conditions(env):
    await publish_version(env, "追问版本", {"type": "object"})
    first = await submit(env)
    await finish(
        env, first, "NEEDS_INPUT", {"question": "哪个地区？", "confirmed_conditions": {"days": 3}}
    )
    second = await submit(env, "two", content="杭州")
    assert second.run.run_id != first.run.run_id
    turn = (await records(env, "conversation_turns", run_id=second.run.run_id))[0]
    assert turn["source_run_id"] == first.run.run_id and turn["confirmed_conditions"] == {"days": 3}
    selected = await env.conversations.select_context(
        env.context, env.cid, second.run.run_id, "必要规则"
    )
    assert selected.current_content_parts == [{"type": "text", "text": "杭州"}]
    assert selected.confirmed_conditions == {"days": 3}
    assert selected.followup_result.data["question"] == "哪个地区？"


async def test_stable_pagination_title_and_channel_subject_isolation(env):
    first = await submit(env)
    await finish(env, first)
    page = await env.conversations.messages(env.context, env.cid, limit=1)
    await submit(env, "two")
    following = await env.conversations.messages(
        env.context, env.cid, cursor=page.next_cursor, limit=10
    )
    assert [m.sequence for m in following.items] == [2]
    detail = await env.conversations.detail(env.context, env.cid)
    await env.conversations.title(
        env.context, env.cid, TitleInput(title="新标题", revision=detail.conversation.revision)
    )
    assert (await env.conversations.messages(env.context, env.cid)).items[0].text == "请帮我处理"
    for scope in (
        Scope(channel_id="other", environment="test"),
        Scope(channel_id=env.context.scope.channel_id, environment="prod"),
        Scope(
            channel_id=env.context.scope.channel_id,
            environment="test",
            subject_type="user",
            subject_id="other",
        ),
    ):
        with pytest.raises(ServiceError) as error:
            await env.conversations.detail(env.context.model_copy(update={"scope": scope}), env.cid)
        assert error.value.status == 404


async def test_context_priorities_summary_sources_and_deletion_guard(env):
    first = await submit(env)
    await finish(env, first)
    summary = await env.conversations.record_summary(
        env.context, env.cid, [first.user_message_id, first.assistant_message_id], "已确认需求"
    )
    second = await submit(env, "two")
    selected = await env.conversations.select_context(
        env.context, env.cid, second.run.run_id, "必要系统规则", recent_messages=0
    )
    assert selected.instructions == "必要系统规则" and selected.current_input == {"value": 1}
    assert selected.summary == summary.content and selected.included_message_ids == [
        second.user_message_id
    ]
    assert selected.truncation["omitted_sequences"] == [1, 2]
    with pytest.raises(ServiceError, match="当前任务和必要指令"):
        await env.conversations.select_context(
            env.context, env.cid, second.run.run_id, "必要系统规则", max_characters=2
        )
    await DeletionService(env.engine, env.authorization).mark(
        env.context, ContentRef("message", first.user_message_id), "测试来源删除"
    )
    with pytest.raises(ServiceError, match="已删除"):
        await env.conversations.select_context(
            env.context, env.cid, second.run.run_id, "必要系统规则", recent_messages=0
        )
    assert not (await env.conversations.detail(env.context, env.cid)).summaries


@pytest.mark.parametrize("running", [False, True])
async def test_ses_a05_delete_blocks_read_execution_and_artifacts_immediately(env, running):
    artifact = await env.conversations.upload(
        env.context, env.cid, "资料.txt", "text/plain", b"secret"
    )
    receipt = await submit(env, attachments=[AttachmentPart(artifact_id=artifact.artifact_id)])
    if running:
        await env.runs.claim_lease(
            TaskEnvelope(channel_id=env.context.scope.channel_id, run_id=receipt.run.run_id),
            "worker",
        )
    preview = await env.conversations.preview_delete(env.context, env.cid)
    assert preview.messages == 2 and preview.runs == 1
    deleted = await env.conversations.delete(env.context, env.cid)
    assert (await env.conversations.delete(env.context, env.cid)).deletion_id == deleted.deletion_id
    for action in (
        env.conversations.detail(env.context, env.cid),
        env.conversations.messages(env.context, env.cid),
        submit(env, "two"),
        env.conversations.download(env.context, env.cid, artifact.artifact_id),
        env.runs.get_run(env.context, receipt.run.run_id),
    ):
        with pytest.raises(ServiceError) as error:
            await action
        assert error.value.status in {404, 410}
    assert (await env.conversations.list_conversations(env.context)).items == []
    await Recovery(env.runs).scan(env.context.scope.channel_id)
    await env.conversations.clean(env.context, ContentRef("conversation", env.cid))
    assert await records(env, "messages") == []
    assert (
        await env.conversations.deletion(env.context, deleted.deletion_id)
    ).status == "WAITING_PROPAGATION"


async def test_stateless_calls_do_not_create_sessions_and_exports_reauthorize(env):
    await env.runs.admit_run(env.context, env.request, "stateless")
    assert len(await records(env, "conversations")) == 1 and not await records(env, "messages")
    receipt = await submit(env)
    await finish(env, receipt)
    export = await env.conversations.export(env.context, env.cid)
    data, _, name = await env.conversations.download(env.context, env.cid, export.artifact_id)
    assert "请帮我处理" in data.decode() and name.endswith(".json")
    env.authorization.denied = True
    with pytest.raises(ServiceError, match="撤销"):
        await env.conversations.download(env.context, env.cid, export.artifact_id)


async def test_management_rerun_creates_new_turn_and_replays(env):
    first = await submit(env)
    await finish(env, first)
    repeated = await env.runs.rerun(env.context, first.run.run_id, "rerun", {"value": 2})
    assert repeated.run_id != first.run.run_id
    replayed = await env.runs.rerun(env.context, first.run.run_id, "rerun", {"value": 2})
    assert replayed.run_id == repeated.run_id
    turns = await records(env, "conversation_turns")
    assert len(turns) == 2 and max(turns, key=lambda t: t["sequence"])["input"] == {"value": 2}


async def test_foreign_agent_and_attachment_are_rejected(env):
    foreign = env.context.model_copy(
        update={"scope": Scope(channel_id="foreign", environment="test")}
    )
    await RecoveryService(env.engine, env.authorization).initialize_fresh(foreign)
    with pytest.raises(ServiceError, match="当前渠道"):
        await env.conversations.create(foreign, ConversationCreate(agent_code="test_agent"))
    with pytest.raises(ServiceError, match="附件不存在"):
        await submit(env, attachments=[AttachmentPart(artifact_id="missing")])
    assert not await records(env, "messages")


async def test_service_subject_isolation_and_management_scope_restore(env):
    scoped = env.context.model_copy(
        update={
            "scope": Scope(
                channel_id=env.context.scope.channel_id,
                environment="test",
                subject_type="customer",
                subject_id="one",
            )
        }
    )
    await RecoveryService(env.engine, env.authorization).initialize_fresh(scoped)
    conversation = await env.conversations.create(
        scoped, ConversationCreate(agent_code="test_agent")
    )
    other = scoped.model_copy(
        update={"scope": scoped.scope.model_copy(update={"subject_id": "two"})}
    )
    with pytest.raises(ServiceError) as error:
        await env.conversations.detail(other, conversation.conversation_id)
    assert error.value.status == 404
    manager = scoped.model_copy(
        update={"scope": scoped.scope.model_copy(update={"subject_type": None, "subject_id": None})}
    )
    assert (
        await env.conversations.detail(manager, conversation.conversation_id)
    ).conversation.conversation_id == conversation.conversation_id


async def test_summary_generation_requires_metered_port_and_deletion_preview_sources(env):
    first = await submit(env)
    await finish(env, first)
    with pytest.raises(ServiceError, match="受控摘要生成服务"):
        await env.conversations.generate_summary(env.context, env.cid, [first.user_message_id])
    hooks = env.conversations.hooks
    async with transaction(env.engine, env.context.scope, hooks.keys(env.context, env.cid)) as uow:
        await hooks.link(
            uow,
            env.context,
            ContentRef("message", first.user_message_id),
            ContentRef("memory", "exclusive"),
        )
        await hooks.link(
            uow,
            env.context,
            ContentRef("message", first.user_message_id),
            ContentRef("memory", "shared"),
        )
        await hooks.link(
            uow, env.context, ContentRef("independent", "another"), ContentRef("memory", "shared")
        )
    impact = await env.conversations.preview_delete(env.context, env.cid)
    assert impact.exclusive_memories == 1 and impact.shared_memories == 1
