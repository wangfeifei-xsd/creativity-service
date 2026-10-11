"""共享资源修改影响后续新受理配置，既有运行与候选快照保持原内容。"""

import pytest

from creativity_service.modules.agents.schemas import AgentValidateInput, AgentVersionEdit
from creativity_service.modules.prompts.schemas import (
    PromptContent,
    PromptDraftEdit,
    PromptVariable,
)
from creativity_service.modules.resources.services import ResourceManagement
from creativity_service.modules.skills.schemas import (
    SkillCreate,
    SkillRelease,
    SkillSettings,
    SkillVariable,
)
from tests.integration.agents.test_agents import publish

pytestmark = pytest.mark.integration


@pytest.mark.parametrize(
    ("input_type", "variable_type", "valid"),
    [("integer", "number", True), ("number", "integer", False), ("string", "number", False)],
)
async def test_prompt_required_variable_uses_schema_compatibility(
    agent_env, input_type, variable_type, valid
):
    env = agent_env
    prompt = await env.prompts.version_detail(env.context, env.prompt_id)
    content = PromptContent.model_validate(prompt.version.content).model_copy(
        update={
            "variables": [PromptVariable(name="quantity", display_name="数量", type=variable_type)]
        }
    )
    await env.prompts.edit_draft(
        env.context, env.prompt_id, PromptDraftEdit(revision=prompt.revision, content=content)
    )
    definition = env.definition.model_copy(
        update={
            "input_schema": {
                **env.definition.input_schema,
                "properties": {
                    **env.definition.input_schema["properties"],
                    "quantity": {"type": input_type, "title": "数量"},
                },
                "required": [*env.definition.input_schema.get("required", []), "quantity"],
            }
        }
    )
    detail = await env.agents.create(
        env.context, env.body.model_copy(update={"definition": definition})
    )
    draft = detail.versions[0]
    result = await env.agents.validate(
        env.context,
        draft.version_id,
        AgentValidateInput(revision=draft.revision, purpose="production"),
    )
    assert result.valid is valid, result
    if not valid:
        assert any("提示词变量“数量”" in i.message for c in result.checks for i in c.issues)


async def test_skill_number_variable_accepts_integer_input_but_rejects_text(agent_env):
    env = agent_env
    skill = await env.skills.create(
        env.context,
        SkillCreate(
            skill_code="numeric_input",
            name="数量核验",
            description="核验输入数量",
            owner="测试人员",
            instructions="数量为 {{quantity}}。",
            settings=SkillSettings(
                input_variables=(SkillVariable(name="quantity", label="数量", value_type="number"),)
            ),
        ),
    )
    await env.skills.freeze(env.context, skill.versions[0].version_id, skill.versions[0].revision)
    await env.skills.release(
        env.context, skill.skill.skill_id, SkillRelease(version_id=skill.versions[0].version_id)
    )
    schema = {
        **env.definition.input_schema,
        "properties": {
            **env.definition.input_schema["properties"],
            "quantity": {"type": "integer", "title": "数量", "enum": [0, 1]},
        },
        "required": [*env.definition.input_schema.get("required", []), "quantity"],
    }
    definition = env.definition.model_copy(
        update={
            "input_schema": schema,
            "bindings": env.definition.bindings.model_copy(
                update={"skill_versions": (skill.skill.skill_id,)}
            ),
        }
    )
    detail = await env.agents.create(
        env.context, env.body.model_copy(update={"definition": definition})
    )
    draft = detail.versions[0]
    result = await env.agents.validate(
        env.context,
        draft.version_id,
        AgentValidateInput(revision=draft.revision, purpose="production"),
    )
    assert result.valid, result
    invalid = definition.model_copy(
        update={
            "input_schema": {
                **schema,
                "properties": {
                    **schema["properties"],
                    "quantity": {"type": "string", "title": "数量"},
                },
            }
        }
    )
    await env.agents.edit_version(
        env.context, draft.version_id, AgentVersionEdit(revision=draft.revision, definition=invalid)
    )
    result = await env.agents.validate(
        env.context,
        draft.version_id,
        AgentValidateInput(revision=draft.revision + 1, purpose="production"),
    )
    assert not result.valid
    assert any("技能必填变量" in issue.message for check in result.checks for issue in check.issues)


async def test_shared_prompt_changes_new_runs_without_republishing_agents(agent_env):
    env = agent_env
    first = await env.agents.create(env.context, env.body)
    second = await env.agents.create(
        env.context,
        env.body.model_copy(update={"agent_code": "another_agent", "name": "另一智能体"}),
    )
    await publish(env, first)
    await publish(env, second)
    before = await env.agents.resolve_published(env.context, env.body.agent_code)
    prompt = await env.prompts.version_detail(env.context, env.prompt_id)
    content = PromptContent.model_validate(prompt.version.content)
    changed = content.model_copy(
        update={
            "instruction_blocks": content.instruction_blocks.model_copy(
                update={"system": "对全部引用方生效的新说明"}
            )
        }
    )
    await env.prompts.edit_draft(
        env.context, env.prompt_id, PromptDraftEdit(revision=prompt.revision, content=changed)
    )
    after = await env.agents.resolve_published(env.context, env.body.agent_code)
    another = await env.agents.resolve_published(env.context, "another_agent")

    def prompt_content(spec):
        return next(v.content for v in spec.versions if v.resource_type == "prompt")

    assert prompt_content(after) == prompt_content(another)
    assert prompt_content(before) != prompt_content(after)
    assert before.source_version_id == after.source_version_id
    assert prompt_content(
        await env.agents.load_candidate(env.context, before.snapshot_id)
    ) == prompt_content(before)
    manager = ResourceManagement(env.engine, env.iam.authorization, {})
    stats = (await manager.summaries(env.context, "prompt", [env.prompt_id]))[0]
    assert stats.reference_count == 2 and stats.usage_count == 0
