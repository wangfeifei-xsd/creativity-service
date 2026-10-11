"""内置助手经过真实 MySQL、IAM、运行和用量链路；模型内容使用可控夹具。"""

import asyncio
import copy
import json
from dataclasses import replace

import httpx
import pytest

from creativity_service.core.context import TaskEnvelope
from creativity_service.core.primitives import ServiceError
from creativity_service.modules.agents.assistance_schemas import AssistanceRequest
from creativity_service.modules.agents.builtin import BUILTIN_CODE, BUILTIN_ID
from creativity_service.modules.agents.schemas import AgentEdit
from creativity_service.modules.models.repositories import repository
from creativity_service.modules.models.schemas import RouteVersionInput
from creativity_service.workers.executor import execute_message
from tests.models.test_protocols import adapter as protocol_adapter

pytestmark = pytest.mark.integration


def proposal(env, *, code="summary_assistant"):
    value = env.body.model_dump(mode="json", by_alias=True)
    value.update(agent_code=code, name="咨询摘要助手", description="整理咨询要点", owner="客服团队")
    value["definition"]["instructions"] = "根据输入的业务诉求总结关键问题，使用中文。"
    value["definition"]["bindings"]["prompt_id"] = None

    def name_fields(schema):
        if isinstance(schema, dict):
            for field in schema.get("properties", {}).values():
                field.setdefault("title", "业务字段")
            for child in schema.values():
                name_fields(child)
        elif isinstance(schema, list):
            for child in schema:
                name_fields(child)

    name_fields(value["definition"])
    return value


def output(value=None, message="请补充需要输出的内容"):
    return {
        "schema_version": "1.0",
        "business_status": "COMPLETED" if value else "NEEDS_INPUT",
        "data": {"message": message, "proposal": value},
        "warnings": [],
        "evidence_refs": [],
    }


async def generate(env, value=None, **kwargs):
    service = env.runtime.assistance
    env.adapter.responses.append(output(value))
    body = AssistanceRequest(
        message="请创建咨询摘要助手", idempotency_key=kwargs.pop("key", "assist"), **kwargs
    )
    receipt = await service.submit(env.context, body)
    await execute_message(
        env.runs,
        TaskEnvelope(channel_id=env.context.scope.channel_id, run_id=receipt.run_id),
        "assistant_worker",
        env.runtime,
    )
    turn = await service.turn(env.context, receipt.run_id)
    assert turn.state == "SUCCEEDED", turn.error
    return receipt, turn


async def test_builtin_readonly_and_excluded_from_business_directory(runtime_env):
    env = runtime_env
    items = await env.agents.list_agents(env.context)
    assert not items.items
    assert {action.action_key for action in items.actions} == {"create", "assist"}
    assert not (await env.agents.list_agents(env.context, search="智能体配置助手")).items
    assert not (await env.agents.list_agents(env.context, published_only=True)).items
    with pytest.raises(ServiceError, match="内置智能体"):
        await env.agents.create(
            env.context, env.body.model_copy(update={"agent_code": BUILTIN_CODE})
        )
    with pytest.raises(ServiceError, match="内置智能体"):
        await env.runtime.assistance.submit(
            env.context,
            AssistanceRequest(
                message="修改你自己的规则", idempotency_key="immutable", agent_id=BUILTIN_ID
            ),
        )


async def test_create_from_server_proposal_is_atomic_and_idempotent(runtime_env):
    env = runtime_env
    value = proposal(env)
    receipt, turn = await generate(env, value)
    assert turn.reply.proposal.definition.instructions
    assert not (await env.agents.list_agents(env.context)).items
    results = await asyncio.gather(
        *(env.runtime.assistance.apply(env.context, receipt.run_id) for _ in range(2))
    )
    assert results[0] == results[1]
    assert [item.agent_id for item in (await env.agents.list_agents(env.context)).items] == [
        results[0].agent_id
    ]
    detail = await env.agents.detail(env.context, results[0].agent_id)
    assert detail.release_version_id is None
    assert len(detail.versions) == 1 and detail.versions[0].status.value == "DRAFT"
    assert detail.versions[0].definition.bindings.prompt_version is None
    result = await env.runs.get_run(env.context, receipt.run_id)
    assert result.usage_summary["input_tokens"] > 0
    request = env.adapter.calls[0][1]
    assert not request.tools and "内置的智能体配置助手" in request.messages[0]["content"]
    assert "provider_credential" not in request.messages[1]["content"]


async def test_followup_keeps_context_and_needs_input_cannot_save(runtime_env):
    env = runtime_env
    first, _ = await generate(env)
    with pytest.raises(ServiceError, match="补充信息"):
        await env.runtime.assistance.apply(env.context, first.run_id)
    second, turn = await generate(env, proposal(env), key="followup", previous_run_id=first.run_id)
    assert turn.reply.proposal is not None
    assert first.run_id != second.run_id
    assert "请补充需要输出的内容" in env.adapter.calls[-1][1].messages[1]["content"]


async def test_modification_adds_draft_and_detects_concurrent_change(runtime_env):
    env = runtime_env
    original = await env.agents.create(env.context, env.body)
    value = proposal(env, code=original.agent.agent_code)
    receipt, turn = await generate(env, value, agent_id=original.agent.agent_id)
    assert turn.base_definition == original.versions[0].definition
    saved = await env.runtime.assistance.apply(env.context, receipt.run_id)
    current = await env.agents.detail(env.context, original.agent.agent_id)
    assert len(current.versions) == 2
    assert (
        next(v for v in current.versions if v.version_id == original.versions[0].version_id)
        == original.versions[0]
    )
    assert saved.agent_id == original.agent.agent_id
    next_receipt, _ = await generate(env, value, key="edit_again", agent_id=original.agent.agent_id)
    await env.agents.edit(
        env.context,
        current.agent.agent_id,
        AgentEdit(
            revision=current.agent.revision,
            name="人工修改",
            description="新的用途",
            owner="客服团队",
        ),
    )
    with pytest.raises(ServiceError, match="已被修改"):
        await env.runtime.assistance.apply(env.context, next_receipt.run_id)
    after = await env.agents.detail(env.context, current.agent.agent_id)
    assert len(after.versions) == 2 and after.agent.name == "人工修改"


async def test_invalid_dependency_is_repaired_without_creating_resources(runtime_env):
    env = runtime_env
    invalid = copy.deepcopy(proposal(env))
    invalid["definition"]["bindings"]["model_route_id"] = "invented_route"
    env.adapter.responses.append(output(invalid))
    receipt, turn = await generate(env, proposal(env))
    assert len(env.adapter.calls) == 2
    assert turn.reply.proposal.definition.bindings.model_route_version == env.route_id
    assert not (await env.agents.list_agents(env.context)).items
    assert (await env.runs.get_run(env.context, receipt.run_id)).usage_summary["attempt_count"] == 2


async def test_unexecutable_token_budget_is_repaired_before_showing_proposal(runtime_env):
    env = runtime_env
    invalid = proposal(env)
    invalid["definition"]["limits"]["token_limit"] = 100
    invalid["definition"]["context"]["context_limit"] = 50
    env.adapter.responses.append(output(invalid))
    _, turn = await generate(env, proposal(env))
    assert len(env.adapter.calls) == 2
    assert turn.reply.proposal.definition.limits.token_limit > 100
    first_request = env.adapter.calls[0][1]
    catalog = json.loads(first_request.messages[1]["content"])["resources"]
    route = next(item for item in catalog if item["version_id"] == env.route_id)
    assert route["configuration"]["max_output_tokens"] == 100
    assert "输出上限" in env.adapter.calls[1][1].messages[-1]["content"]


@pytest.mark.parametrize("configured_limit", [4096, 32768])
async def test_full_proposal_has_its_own_output_budget(runtime_env, configured_limit):
    env = runtime_env
    async with env.engine.connect() as connection:
        route = await repository(env.context.scope, "resource_versions").get(
            connection, env.route_id
        )
    await env.models.routing.create_version(
        env.tenant.manager,
        env.route_id,
        RouteVersionInput(
            revision=route["revision"],
            primary_model=env.model.id,
            required_capabilities=["text"],
            parameters={"max_tokens": configured_limit},
        ),
    )
    calls = []
    candidate = proposal(env)
    candidate["definition"]["limits"]["token_limit"] = max(
        candidate["definition"]["limits"]["token_limit"], configured_limit + 8000
    )

    async def handler(request):
        body = json.loads(request.content)
        calls.append(body)
        # 模拟完整方案和推理共需六千 Token，复现四千上限下的截断。
        truncated = body["max_tokens"] < 6000
        return httpx.Response(
            200,
            json={
                "id": "assistance-output",
                "object": "chat.completion",
                "created": 1,
                "model": "fixture",
                "choices": [
                    {
                        "index": 0,
                        "message": {
                            "role": "assistant",
                            "content": '{"business_status":'
                            if truncated
                            else json.dumps(output(candidate), ensure_ascii=False),
                        },
                        "finish_reason": "length" if truncated else "stop",
                    }
                ],
                "usage": {"prompt_tokens": 100, "completion_tokens": 6000},
            },
        )

    env.runtime.models.models = replace(env.models, adapter=protocol_adapter(handler))
    receipt, turn = await generate(env, proposal(env))
    assert turn.reply.proposal is not None
    assert len(calls) == 1 and calls[0]["max_tokens"] == max(16384, configured_limit)
    result = await env.runs.get_run(env.context, receipt.run_id)
    assert result.usage_summary["attempt_count"] == 1
    assert result.usage_summary["output_tokens"] == 6000
    async with env.engine.connect() as connection:
        unchanged = await repository(env.context.scope, "resource_versions").get(
            connection, env.route_id
        )
    assert unchanged["content"]["parameters"]["max_tokens"] == configured_limit


async def test_http_apply_rejects_client_proposal_and_other_identity(runtime_env):
    env = runtime_env
    env.adapter.responses.append(output(proposal(env)))
    response = await env.client.post(
        "/admin/v1/agents/assistance",
        json={"message": "创建摘要助手", "idempotency_key": "http_assist"},
    )
    assert response.status_code == 202, response.text
    run_id = response.json()["run_id"]
    await execute_message(
        env.runs,
        TaskEnvelope(channel_id=env.context.scope.channel_id, run_id=run_id),
        "http_worker",
        env.runtime,
    )
    read = await env.client.get(f"/admin/v1/agents/assistance/runs/{run_id}")
    assert read.status_code == 200 and read.json()["reply"]["proposal"]
    injected = await env.client.post(
        f"/admin/v1/agents/assistance/runs/{run_id}/apply", json={"agent_id": "other_agent"}
    )
    assert injected.status_code == 422
    assert not (await env.agents.list_agents(env.context)).items
    other = env.context.model_copy(update={"principal_id": "another_manager"})
    with pytest.raises(ServiceError):
        await env.runtime.assistance.apply(other, run_id)
    response = await env.client.post(
        "/admin/v1/agents/assistance",
        json={
            "message": "越权修改",
            "idempotency_key": "injection",
            "channel_id": "another_channel",
        },
    )
    assert response.status_code == 422


async def test_failed_schema_and_foreign_channel_cannot_produce_draft(runtime_env):
    env = runtime_env
    env.adapter.responses.extend([{"data": "错误结构"}] * 3)
    receipt = await env.runtime.assistance.submit(
        env.context, AssistanceRequest(message="创建智能体", idempotency_key="bad_output")
    )
    await execute_message(
        env.runs,
        TaskEnvelope(channel_id=env.context.scope.channel_id, run_id=receipt.run_id),
        "bad_worker",
        env.runtime,
    )
    turn = await env.runtime.assistance.turn(env.context, receipt.run_id)
    assert turn.state == "FAILED" and turn.reply is None
    with pytest.raises(ServiceError):
        await env.runtime.assistance.apply(env.context, receipt.run_id)
    foreign = env.context.model_copy(
        update={"scope": env.context.scope.model_copy(update={"channel_id": "another_channel"})}
    )
    with pytest.raises(ServiceError):
        await env.runtime.assistance.turn(foreign, receipt.run_id)


async def test_changed_dependency_blocks_save_and_generated_instructions_execute(runtime_env):
    from creativity_service.modules.agents.repositories import repository
    from creativity_service.modules.agents.schemas import AgentTestInput
    from creativity_service.modules.models.schemas import RouteVersionInput

    env = runtime_env
    receipt, _ = await generate(env, proposal(env))
    saved = await env.runtime.assistance.apply(env.context, receipt.run_id)
    detail = await env.agents.detail(env.context, saved.agent_id)
    draft = detail.versions[0]
    test = await env.agents.test(
        env.context,
        draft.version_id,
        AgentTestInput(
            revision=draft.revision,
            input={"request": "请归纳咨询要点"},
            idempotency_key="generated_debug",
        ),
    )
    await execute_message(
        env.runs,
        TaskEnvelope(channel_id=env.context.scope.channel_id, run_id=test.run_id),
        "draft_worker",
        env.runtime,
    )
    assert (await env.runs.get_run(env.context, test.run_id)).state == "SUCCEEDED"
    assert draft.definition.instructions in env.adapter.calls[-1][1].messages[0]["content"]
    pending, _ = await generate(env, proposal(env, code="another_summary"), key="stale_dependency")
    async with env.engine.connect() as connection:
        current_route = await repository("resource_versions", env.context.scope).get(
            connection, env.route_id
        )
    await env.models.routing.create_version(
        env.tenant.manager,
        env.route_id,
        RouteVersionInput(
            revision=current_route["revision"],
            primary_model=env.model.id,
            required_capabilities=["text", "structured_output", "tools"],
            label="更新后的路由",
            parameters={"max_tokens": 200},
        ),
    )
    with pytest.raises(ServiceError, match="资源已变更"):
        await env.runtime.assistance.apply(env.context, pending.run_id)
