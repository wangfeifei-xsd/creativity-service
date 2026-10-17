"""智能提取提示词经过真实事务、授权和运行校验，保存前只保留候选。"""

import asyncio
import copy

import pytest

from creativity_service.core.context import TaskEnvelope
from creativity_service.core.database import Repository, transaction
from creativity_service.core.locking import record_key
from creativity_service.core.primitives import ServiceError
from creativity_service.modules.agents.assistance_prompts import new_prompts
from creativity_service.modules.agents.assistance_schemas import AssistanceRequest
from creativity_service.modules.agents.repositories import repository
from creativity_service.modules.agents.schemas import AgentTestInput
from creativity_service.modules.iam.repositories import policy_key
from creativity_service.modules.iam.tables import metadata
from creativity_service.modules.resources.configuration import reference_rows
from creativity_service.workers.executor import execute_message
from tests.integration.runtime.test_agent_assistance import output, proposal

pytestmark = pytest.mark.integration


def candidate(env):
    result = output(proposal(env))
    definition = result["data"]["proposal"]["definition"]
    summary_schema = {
        "type": "object",
        "properties": {"summary": {"type": "string", "title": "咨询摘要"}},
        "required": ["summary"],
        "additionalProperties": False,
    }
    review = copy.deepcopy(definition["steps"][0])
    review.update(
        key="review",
        name="复核摘要",
        input_schema=summary_schema,
        inputs={"summary": {"source": "step", "step": "answer", "path": "summary"}},
    )
    definition["steps"][0]["output_schema"] = summary_schema
    definition["steps"].append(review)
    definition.update(
        workflow_type="template",
        entrypoint="workflow.v1",
        edges=[{"source": "answer", "target": "review"}, {"source": "review", "target": "END"}],
    )
    result["data"]["prompt_drafts"] = [
        {
            "name": "咨询分析",
            "purpose": "归纳问题、事实和待办事项",
            "step_key": "answer",
            "content": {
                "instruction_blocks": {"system": "先提取事实，再分类问题，最后列出待办事项。"},
                "message_templates": [{"source": "input", "template": "业务诉求：{{request}}"}],
                "variables": [{"name": "request", "display_name": "业务诉求", "type": "string"}],
            },
        },
        {
            "name": "摘要质量检查提示词",
            "purpose": "检查摘要是否遗漏重要事实",
            "step_key": "review",
            "content": {
                "instruction_blocks": {"system": "检查摘要遗漏、矛盾和无依据的推测。"},
                "message_templates": [{"source": "input", "template": "待复核摘要：{{summary}}"}],
                "variables": [{"name": "summary", "display_name": "咨询摘要", "type": "string"}],
            },
        },
    ]
    return result


async def generate_candidate(env, result):
    env.adapter.responses.append(result)
    receipt = await env.runtime.assistance.submit(
        env.context,
        AssistanceRequest(message="创建带复杂分析规则的摘要助手", idempotency_key="abstract"),
    )
    await execute_message(
        env.runs,
        TaskEnvelope(channel_id=env.context.scope.channel_id, run_id=receipt.run_id),
        "prompt-worker",
        env.runtime,
    )
    turn = await env.runtime.assistance.turn(env.context, receipt.run_id)
    assert turn.state == "SUCCEEDED", turn.error
    return receipt, turn


async def extracted_prompts(env):
    async with env.engine.connect() as connection:
        return [
            row
            for row in await repository("prompts", env.context.scope).find(connection)
            if row["prompt_code"].startswith("assisted_")
        ]


async def test_named_prompt_drafts_publish_and_bind_atomically(runtime_env):
    env = runtime_env
    value = candidate(env)
    receipt, turn = await generate_candidate(env, value)
    names = ["咨询摘要助手抽象的咨询分析提示词", "咨询摘要助手抽象的摘要质量检查提示词"]
    assert [draft.name for draft in turn.reply.prompt_drafts] == names
    assert not await extracted_prompts(env)
    saved, repeated = await asyncio.gather(
        *(env.runtime.assistance.apply(env.context, receipt.run_id) for _ in range(2))
    )
    assert saved == repeated
    assert [p.name for p in saved.prompts] == names
    assert len(await extracted_prompts(env)) == 2
    identifiers = [p.prompt_id for p in saved.prompts]
    async with env.engine.connect() as connection:
        configs = await repository("resource_versions", env.context.scope).get_many(
            connection, identifiers
        )
        releases = await repository("release_mappings", env.context.scope).find_many(
            connection, "resource_id", identifiers
        )
        audits = await repository("audit_events", env.context.scope).find_many(
            connection, "target_id", identifiers
        )
        references = await reference_rows(connection, env.context.scope, identifiers)
    assert {row["resource_id"] for row in releases} == set(identifiers)
    assert all(row["environment"] == env.context.scope.environment for row in releases)
    assert all(row["state"] == "PUBLISHED" for row in configs.values())
    assert len([row for row in audits if row["action"] == "resource.publish"]) == 2
    assert {row["target_id"] for row in references} == set(identifiers)
    assert configs[identifiers[0]]["content"]["variables"][0]["display_name"] == "业务诉求"
    detail = await env.agents.detail(env.context, saved.agent_id)
    assert (
        detail.versions[0].definition.instructions
        == value["data"]["proposal"]["definition"]["instructions"]
    )
    draft = detail.versions[0]
    assert draft.status.value == "DRAFT"
    assert [step.prompt_id for step in draft.definition.steps] == identifiers
    assert set(identifiers) <= set(draft.definition.dependency_ids())
    env.adapter.responses.extend(
        [
            {"summary": "客户咨询退款进度"},
            {
                "business_status": "COMPLETED",
                "schema_version": "1.0",
                "data": {"answer": "待核实退款进度"},
                "warnings": [],
                "evidence_refs": [],
            },
        ]
    )
    test = await env.agents.test(
        env.context,
        draft.version_id,
        AgentTestInput(
            revision=draft.revision,
            input={"request": "请查询退款进度"},
            idempotency_key="extracted_prompts_debug",
        ),
    )
    await execute_message(
        env.runs,
        TaskEnvelope(channel_id=env.context.scope.channel_id, run_id=test.run_id),
        "generated-prompt-worker",
        env.runtime,
    )
    result = await env.runs.get_run(env.context, test.run_id)
    assert result.state == "SUCCEEDED", result.error
    first, second = [str(call[1].messages) for call in env.adapter.calls[-2:]]
    assert "先提取事实，再分类问题" in first and "业务诉求：请查询退款进度" in first
    assert "检查摘要遗漏" in second and "待复核摘要：客户咨询退款进度" in second
    assert "检查摘要遗漏" not in first and "先提取事实，再分类问题" not in second


@pytest.mark.parametrize("failure", ["prompt:manage", "release:publish", "run:create", "commit"])
async def test_save_failure_leaves_no_extracted_resources(runtime_env, monkeypatch, failure):
    env = runtime_env
    receipt, turn = await generate_candidate(env, candidate(env))
    identifiers = [
        prompt.identifier
        for prompt in new_prompts(env.context, receipt.run_id, turn.reply.prompt_drafts)
    ]
    if failure != "commit":
        grants = await env.iam.accounts.repository.grants(env.context.scope.channel_id)
        owned = [grant for grant in grants if grant.grantee_id == env.context.actor_id]
        assert owned
        repo = Repository(metadata.tables["resource_grants"], env.context.scope)
        async with transaction(
            env.engine,
            env.context.scope,
            [policy_key(env.context.scope.channel_id)]
            + [
                record_key(env.context.scope.channel_id, "resource_grants", grant.id)
                for grant in owned
            ],
        ) as uow:
            for grant in owned:
                await repo.change(
                    uow,
                    grant.id,
                    grant.revision,
                    {
                        "allowed_actions": [
                            action for action in grant.allowed_actions if action != failure
                        ]
                    },
                )
    else:

        async def interrupted(*args, **kwargs):
            # 此时提示词及智能体主记录已写入事务，后续失败必须一起回滚。
            raise ServiceError("SAVE_INTERRUPTED", "模拟草稿保存失败", 409)

        monkeypatch.setattr(env.agents, "add_draft", interrupted)
    with pytest.raises(ServiceError) as error:
        await env.runtime.assistance.apply(env.context, receipt.run_id)
    assert error.value.code == ("SAVE_INTERRUPTED" if failure == "commit" else "FORBIDDEN")
    assert not await extracted_prompts(env)
    async with env.engine.connect() as connection:
        assert not await repository("resource_versions", env.context.scope).get_many(
            connection, identifiers
        )
        assert not await repository("release_mappings", env.context.scope).find_many(
            connection, "resource_id", identifiers
        )
        assert not await repository("audit_events", env.context.scope).find_many(
            connection, "target_id", identifiers
        )
    assert not (await env.agents.list_agents(env.context)).items
    assert (await env.runtime.assistance.turn(env.context, receipt.run_id)).reply.prompt_drafts


@pytest.mark.parametrize(
    "invalid", ["template", "step", "missing_step", "duplicate_step", "input", "long_instructions"]
)
async def test_invalid_extraction_is_repaired_before_showing_plan(runtime_env, invalid):
    env = runtime_env
    valid = candidate(env)
    bad = copy.deepcopy(valid)
    if invalid == "template":
        bad["data"]["prompt_drafts"][0]["content"]["message_templates"][0]["template"] = (
            "{{undeclared}}"
        )
    elif invalid == "step":
        bad["data"]["prompt_drafts"][0]["step_key"] = "nonexistent"
    elif invalid == "missing_step":
        bad["data"]["prompt_drafts"][0].pop("step_key")
    elif invalid == "duplicate_step":
        bad["data"]["prompt_drafts"][1]["step_key"] = "answer"
    elif invalid == "input":
        bad["data"]["prompt_drafts"][1]["content"]["variables"][0]["type"] = "integer"
    else:
        instructions = "先提取业务事实，再按规则分析，最后验证结果。" * 65
        valid["data"]["proposal"]["definition"]["instructions"] = instructions
        bad["data"]["proposal"]["definition"]["instructions"] = instructions
        bad["data"]["prompt_drafts"] = []
    env.adapter.responses.append(bad)
    _, turn = await generate_candidate(env, valid)
    assert len(env.adapter.calls) == 2
    assert len(turn.reply.prompt_drafts) == 2
    assert not await extracted_prompts(env)


async def test_single_model_step_is_inferred_for_legacy_prompt_candidate(runtime_env):
    env = runtime_env
    value = candidate(env)
    value["data"]["proposal"] = proposal(env)
    value["data"]["prompt_drafts"] = value["data"]["prompt_drafts"][:1]
    value["data"]["prompt_drafts"][0].pop("step_key")
    receipt, turn = await generate_candidate(env, value)
    assert turn.reply.prompt_drafts[0].step_key == "answer"
    saved = await env.runtime.assistance.apply(env.context, receipt.run_id)
    detail = await env.agents.detail(env.context, saved.agent_id)
    assert detail.versions[0].definition.steps[0].prompt_id == saved.prompts[0].prompt_id
