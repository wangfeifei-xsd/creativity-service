"""共享资源修改影响后续新受理配置，既有运行与候选快照保持原内容。"""

import pytest

from creativity_service.modules.prompts.schemas import PromptContent, PromptDraftEdit
from creativity_service.modules.resources.services import ResourceManagement
from tests.integration.agents.test_agents import publish

pytestmark = pytest.mark.integration


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
