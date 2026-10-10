"""提示词实验固定基线和唯一变量，所有候选经真实评测运行与账本归集。"""

import pytest

from creativity_service.core.primitives import ServiceError
from creativity_service.modules.evaluations.schemas import EvaluationCreate
from creativity_service.modules.prompts.schemas import PromptContent, PromptCreate, PromptDraftEdit
from creativity_service.modules.resources.schemas import ResourceMutation
from creativity_service.modules.resources.services import ResourceManagement
from tests.integration.channels.test_resource_management import ValidConfiguration
from tests.integration.evaluations.test_evaluations import finish, prepare

pytestmark = [
    pytest.mark.integration,
    pytest.mark.parametrize(
        "agent_env",
        [
            {
                "environment": "prod",
                "independent_actions": ["release:publish", "data:read_sensitive"],
            }
        ],
        indirect=True,
    ),
]


async def test_prompt_experiment_runs_without_release_and_rejects_other_changes(evaluation_env):
    env = evaluation_env
    first, dataset, _ = await prepare(env, 1)
    prompt = (await env.prompts.list_items(env.context)).items[0]
    variant = await env.prompts.create(
        env.context,
        PromptCreate(prompt_code="experiment_variant", name="实验措辞", purpose="独立对照提示词"),
    )
    draft = await env.prompts.version_detail(env.context, variant.prompt_id)
    draft = await env.prompts.edit_draft(
        env.context,
        variant.prompt_id,
        PromptDraftEdit(
            revision=draft.revision,
            content=PromptContent(instruction_blocks={"system": "请按结构回答。"}),
        ),
    )
    # 发布门禁另有专门验收，此处用已发布独立资源验证实验对照与运行快照。
    management = ResourceManagement(
        env.engine, env.iam.authorization, {"prompt": ValidConfiguration()}
    )
    await management.mutate(
        env.context,
        "prompt",
        variant.prompt_id,
        "publish",
        ResourceMutation(revision=variant.revision, configuration_revision=draft.revision),
    )
    definition = env.definition.model_copy(
        update={
            "bindings": env.definition.bindings.model_copy(
                update={"prompt_version": variant.prompt_id}
            )
        }
    )
    second = await env.agents.create(
        env.context,
        env.body.model_copy(update={"agent_code": "prompt_variant", "definition": definition}),
    )
    candidates = [
        {"version_id": item.versions[0].version_id, "revision": item.versions[0].revision}
        for item in (first, second)
    ]
    body = EvaluationCreate(
        name="提示词实验",
        dataset_version_id=dataset.current_version_id,
        candidates=candidates,
        baseline_candidate_version_id=candidates[0]["version_id"],
        experiment_prompt_id=prompt.prompt_id,
        budget={"max_runs": 3, "token_limit": 1000000},
    )
    task = await env.evaluations.create(env.context, body)
    await finish(env, task)
    report = await env.evaluations.comparison(env.context, task.evaluation_id)
    assert report.complete and report.cost["run_count"] == 2
    assert len(report.candidates) == 2
    assert not any(c["release_passed"] for c in report.candidates)
    duplicate = await env.agents.create(
        env.context, env.body.model_copy(update={"agent_code": "duplicate_prompt_variant"})
    )
    with pytest.raises(ServiceError) as failure:
        await env.evaluations.create(
            env.context,
            body.model_copy(
                update={
                    "candidates": [
                        body.candidates[0],
                        body.candidates[1].model_copy(
                            update={
                                "version_id": duplicate.versions[0].version_id,
                                "revision": duplicate.versions[0].revision,
                            }
                        ),
                    ]
                }
            ),
        )
    assert failure.value.code == "EXPERIMENT_DUPLICATE"
    for change in (
        {"release_target": True},
        {"experiment_prompt_id": "missing-prompt"},
        {"baseline_candidate_version_id": None},
    ):
        with pytest.raises(ServiceError):
            await env.evaluations.create(env.context, body.model_copy(update=change))
    altered = await env.agents.create(
        env.context,
        env.body.model_copy(
            update={
                "agent_code": "incomparable_variant",
                "definition": definition.model_copy(
                    update={"limits": definition.limits.model_copy(update={"max_model_rounds": 4})}
                ),
            }
        ),
    )
    with pytest.raises(ServiceError) as failure:
        await env.evaluations.create(
            env.context,
            body.model_copy(
                update={
                    "candidates": [
                        body.candidates[0],
                        body.candidates[1].model_copy(
                            update={
                                "version_id": altered.versions[0].version_id,
                                "revision": altered.versions[0].revision,
                            }
                        ),
                    ]
                }
            ),
        )
    assert failure.value.code == "EXPERIMENT_NOT_COMPARABLE"
