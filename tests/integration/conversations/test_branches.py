"""会话分支的幂等、上下文继承与单向删除传播。"""

import asyncio
import json

import pytest
from sqlalchemy import delete

from creativity_service.core.context import TaskEnvelope
from creativity_service.core.contracts import BusinessResult
from creativity_service.core.database import transaction
from creativity_service.core.database.tables import metadata
from creativity_service.core.deletion import ContentRef, DeletionGuard, DeletionService
from creativity_service.core.primitives import ServiceError
from creativity_service.modules.conversations.schemas import BranchInput
from tests.integration.conversations.test_conversations import finish, message, records, submit

pytestmark = pytest.mark.integration


async def test_non_streamed_reply_is_preserved_in_history_and_branch(env):
    receipt = await submit(env)
    lease = await env.runs.claim_lease(
        TaskEnvelope(channel_id=env.context.scope.channel_id, run_id=receipt.run.run_id),
        "structured-worker",
    )
    assert lease
    result = BusinessResult(
        schema_version="1",
        business_status="COMPLETED",
        data={"value": 7},
        warnings=(),
        evidence_refs=(),
    )
    await env.runs.finish_run(lease, "SUCCEEDED", result)
    branch = await env.conversations.branch(
        env.context,
        env.cid,
        BranchInput(message_id=receipt.assistant_message_id, idempotency_key="structured"),
    )
    copied = await records(env, "messages", conversation_id=branch.conversation_id)
    assistant = next(item for item in copied if item["role"] == "assistant")
    assert json.loads(assistant["content_parts"][0]["text"])["data"] == {"value": 7}
    assert assistant["run_id"] is None
    following = await env.conversations.submit(env.context, branch.conversation_id, message("two"))
    next_lease = await env.runs.claim_lease(
        TaskEnvelope(channel_id=env.context.scope.channel_id, run_id=following.run.run_id),
        "following-worker",
    )
    assert next_lease
    selected = await env.conversations.select_context(
        env.context, branch.conversation_id, next_lease.run_id, "必要指令"
    )
    reply = next(item for item in selected.messages if item["role"] == "assistant")
    assert json.loads(reply["content_parts"][0]["text"])["data"] == {"value": 7}


async def test_branch_replay_context_and_no_shared_run_ownership(env):
    receipt = await submit(env)
    await finish(env, receipt)
    body = BranchInput(message_id=receipt.assistant_message_id, idempotency_key="branch-one")
    result = await asyncio.gather(
        *(env.conversations.branch(env.context, env.cid, body) for _ in range(4))
    )
    assert len({item.conversation_id for item in result}) == 1
    branch = result[0].conversation_id
    copied = await records(env, "messages", conversation_id=branch)
    assert len(copied) == 2 and all(item["run_id"] is None for item in copied)
    with pytest.raises(ServiceError, match="内容不能改变"):
        await env.conversations.branch(
            env.context, env.cid, body.model_copy(update={"title": "不同标题"})
        )
    following = await env.conversations.submit(env.context, branch, message("two"))
    lease = await env.runs.claim_lease(
        TaskEnvelope(channel_id=env.context.scope.channel_id, run_id=following.run.run_id),
        "branch-worker",
    )
    assert lease
    selected = await env.conversations.select_context(env.context, branch, lease.run_id, "必要指令")
    assert len(selected.messages) == 2
    await DeletionService(env.engine, env.authorization).mark(
        env.context, ContentRef("conversation", branch), "TEST"
    )
    assert (await env.conversations.detail(env.context, env.cid)).conversation.title == "未命名会话"


async def test_source_delete_blocks_branch_and_cross_scope_cannot_branch(env):
    receipt = await submit(env)
    with pytest.raises(ServiceError, match="已完成"):
        await env.conversations.branch(
            env.context,
            env.cid,
            BranchInput(message_id=receipt.assistant_message_id, idempotency_key="unfinished"),
        )
    await finish(env, receipt)
    body = BranchInput(message_id=receipt.assistant_message_id, idempotency_key="one")
    branch = await env.conversations.branch(env.context, env.cid, body)
    foreign = env.context.model_copy(
        update={"scope": env.context.scope.model_copy(update={"channel_id": "other"})}
    )
    with pytest.raises(ServiceError) as denied:
        await env.conversations.branch(foreign, env.cid, body)
    assert denied.value.status == 404
    await DeletionService(env.engine, env.authorization).mark(
        env.context, ContentRef("message", receipt.assistant_message_id), "TEST"
    )
    copied = await records(env, "messages", conversation_id=branch.conversation_id)
    assistant = next(item for item in copied if item["role"] == "assistant")
    async with transaction(
        env.engine,
        env.context.scope,
        env.conversations.hooks.keys(env.context, branch.conversation_id),
    ) as uow:
        with pytest.raises(ServiceError) as deleted:
            await DeletionGuard(env.context.scope).check(
                uow, [ContentRef("message", assistant["id"])]
            )
        assert deleted.value.code == "CONTENT_DELETED"


@pytest.mark.parametrize("legacy", [False, True])
async def test_branch_deletion_tracks_copied_messages_without_owning_source_run(env, legacy):
    receipt = await submit(env)
    await finish(env, receipt)
    branch = await env.conversations.branch(
        env.context,
        env.cid,
        BranchInput(message_id=receipt.assistant_message_id, idempotency_key="branch-deletion"),
    )
    if legacy:
        # 历史分支缺少会话归属边，消息表仍保存权威的会话标识。
        table = metadata.tables["source_links"]
        async with env.engine.begin() as connection:
            await connection.execute(
                delete(table).where(
                    table.c.channel_id == env.context.scope.channel_id,
                    table.c.source_type == "conversation",
                    table.c.source_id == branch.conversation_id,
                    table.c.derived_type == "message",
                )
            )
    impact = await env.conversations.preview_delete(env.context, branch.conversation_id)
    assert impact.messages == 2
    assert impact.runs == 0
    copied = await records(env, "messages", conversation_id=branch.conversation_id)
    await DeletionService(env.engine, env.authorization).mark(
        env.context, ContentRef("conversation", branch.conversation_id), "TEST"
    )
    async with transaction(
        env.engine,
        env.context.scope,
        env.conversations.hooks.keys(env.context, branch.conversation_id),
    ) as uow:
        guard = DeletionGuard(env.context.scope)
        for item in copied:
            with pytest.raises(ServiceError) as deleted:
                await guard.check(uow, [ContentRef("message", item["id"])])
            assert deleted.value.code == "CONTENT_DELETED"
        await guard.check(uow, [ContentRef("message", receipt.assistant_message_id)])
    assert (await env.conversations.detail(env.context, env.cid)).conversation.status == "ACTIVE"
