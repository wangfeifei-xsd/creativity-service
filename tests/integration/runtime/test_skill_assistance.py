"""技能智能创建与修改使用真实 MySQL、IAM 和运行事务，模型与文件存储使用明确夹具。"""

import asyncio
import copy
import json

import pytest

from creativity_service.core.context import TaskEnvelope
from creativity_service.core.database import Repository, transaction
from creativity_service.core.deletion import ContentRef
from creativity_service.core.locking import record_key
from creativity_service.core.primitives import ServiceError
from creativity_service.modules.iam.repositories import policy_key
from creativity_service.modules.iam.tables import metadata
from creativity_service.modules.resources.schemas import ResourceMutation
from creativity_service.modules.resources.services import ResourceManagement
from creativity_service.modules.skills.assistance_schemas import (
    SkillAssistanceRequest,
    SkillProposal,
)
from creativity_service.modules.skills.packages import entry, validate_files
from creativity_service.modules.skills.persistence import PackageWrite
from creativity_service.modules.skills.schemas import SkillEdit, SkillVersionEdit
from creativity_service.modules.skills.services import SkillVersionValidator
from creativity_service.workers.executor import execute_message
from tests.integration.runtime.test_agent_assistance import generate as generate_agent
from tests.integration.runtime.test_agent_assistance import output

pytestmark = pytest.mark.integration


def proposal():
    return SkillProposal(
        skill_code="reply_review",
        name="客服回复检查",
        description="检查遗漏与无依据的承诺",
        owner="客服团队",
        instructions=(
            "## 使用场景\n客户回复发送前检查。\n\n## 步骤\n1. 核对问题。"
            "\n2. 标出无依据的承诺。\n\n## 输出\n给出修改建议。"
        ),
    ).model_dump(mode="json")


async def generate(env, value, **kwargs):
    env.client._transport.app.state.skills = env.skills
    env.adapter.responses.append(output(value, "已整理技能书，请核对。"))
    body = SkillAssistanceRequest(
        message="生成客服回复检查技能书",
        idempotency_key=kwargs.pop("key", "skill-assist"),
        **kwargs,
    )
    receipt = await env.runtime.skill_assistance.submit(env.context, body)
    await execute_message(
        env.runs,
        TaskEnvelope(channel_id=env.context.scope.channel_id, run_id=receipt.run_id),
        "skill-worker",
        env.runtime,
    )
    turn = await env.runtime.skill_assistance.turn(env.context, receipt.run_id)
    assert turn.state == "SUCCEEDED", turn.error
    return receipt, turn


async def revoke(env, action):
    grants = await env.iam.accounts.repository.grants(env.context.scope.channel_id)
    owned = [grant for grant in grants if grant.grantee_id == env.context.actor_id]
    assert owned
    async with transaction(
        env.engine,
        env.context.scope,
        [
            policy_key(env.context.scope.channel_id),
            *[
                record_key(env.context.scope.channel_id, "resource_grants", grant.id)
                for grant in owned
            ],
        ],
    ) as uow:
        for grant in owned:
            await Repository(metadata.tables["resource_grants"], env.context.scope).change(
                uow,
                grant.id,
                grant.revision,
                {"allowed_actions": [a for a in grant.allowed_actions if a != action]},
            )


async def test_create_preview_usage_and_concurrent_idempotent_save(runtime_env):
    env = runtime_env
    value = proposal()
    receipt, turn = await generate(env, value)
    assert turn.reply.proposal.instructions == value["instructions"]
    assert not (await env.skills.list_skills(env.context)).items
    assert not (await env.agents.list_agents(env.context)).items
    responses = await asyncio.gather(
        *[
            env.client.post(f"/admin/v1/skills/assistance/runs/{receipt.run_id}/apply")
            for _ in range(2)
        ]
    )
    assert [r.status_code for r in responses] == [200, 200], [r.text for r in responses]
    assert responses[0].json() == responses[1].json()
    identifier = responses[0].json()["skill_id"]
    detail = await env.skills.detail(env.context, identifier)
    assert detail.release_version_id is None and len(detail.versions) == 1
    assert detail.versions[0].status.value == "DRAFT"
    file = await env.skills.file(env.context, identifier, "SKILL.md")
    assert value["instructions"] in file.text
    assert '"name": "客服回复检查"' in file.text
    assert len((await env.skills.list_skills(env.context)).items) == 1
    assert (await env.runs.get_run(env.context, receipt.run_id)).usage_summary["input_tokens"] > 0
    assert not env.adapter.calls[0][1].tools
    assert "技能配置助手" in env.adapter.calls[0][1].messages[0]["content"]
    _, descriptor, values, _ = await env.runtime.skill_assistance.record(
        env.context, receipt.run_id
    )
    assert "agents" not in values and "allowed_agents" not in descriptor
    assert "allowed_agents" not in detail.versions[0].settings.model_dump()


async def test_edit_preserves_references_metadata_and_does_not_replay_over_later_edits(runtime_env):
    env = runtime_env
    original = proposal()
    created = await env.skills.create(env.context, SkillProposal.model_validate(original))
    identifier = created.skill.skill_id
    source = entry(original["name"], original["description"], original["instructions"]).decode()
    source = source.replace('"name":', '"license": "MIT", "name":')
    package = validate_files(
        {
            "SKILL.md": source.encode(),
            "references/checklist.md": "核对事实".encode(),
            "images/test.bin": b"\x00\x01",
        }
    )
    await env.skills.write_package(
        env.context, identifier, identifier, "当前配置", package, revision=1
    )
    changed = {
        **original,
        "name": "客服回复质量检查",
        "instructions": original["instructions"] + "\n\n参考[检查清单](references/checklist.md)。",
    }
    receipt, turn = await generate(env, changed, skill_id=identifier, base_revision=2)
    assert turn.base.name == original["name"]
    messages = env.adapter.calls[0][1].messages[1]["content"]
    assert "核对事实" in messages and "package_url" not in messages
    await env.runtime.skill_assistance.apply(env.context, receipt.run_id)
    view = await env.skills.version(env.context, identifier)
    assert view.revision == 3 and view.metadata["license"] == "MIT"
    assert view.metadata["name"] == changed["name"]
    assert {f.relative_path for f in view.files} == set(package.files)
    assert (
        await env.skills.file(env.context, identifier, "references/checklist.md")
    ).text == "核对事实"
    await env.skills.edit_version(
        env.context,
        identifier,
        SkillVersionEdit(
            revision=3, settings=view.settings.model_copy(update={"change_note": "后续人工调整"})
        ),
    )
    await env.runtime.skill_assistance.apply(env.context, receipt.run_id)
    assert (
        await env.skills.version(env.context, identifier)
    ).settings.change_note == "后续人工调整"


async def test_clarification_and_continuation_keep_target_and_use_new_run(runtime_env):
    env = runtime_env
    first, turn = await generate(env, None)
    assert turn.reply.proposal is None
    with pytest.raises(ServiceError, match="补充"):
        await env.runtime.skill_assistance.apply(env.context, first.run_id)
    second, _ = await generate(env, proposal(), key="skill-continued", previous_run_id=first.run_id)
    values = json.loads(env.adapter.calls[-1][1].messages[1]["content"])
    assert len(values["history"]) == 2
    assert second.run_id != first.run_id


@pytest.mark.parametrize("invalid", ["placeholder", "reference", "tool", "budget", "state"])
async def test_invalid_skill_is_repaired_before_preview(runtime_env, invalid):
    env = runtime_env
    bad = copy.deepcopy(proposal())
    if invalid == "placeholder":
        bad["instructions"] += "{{undeclared}}"
    elif invalid == "reference":
        bad["instructions"] += "[缺失资料](../outside.md)"
    elif invalid == "tool":
        bad["settings"]["tool_bindings"] = {"secret": "foreign_tool"}
    elif invalid == "budget":
        bad["settings"]["context_budget"] = 1
    invalid_output = output(bad)
    if invalid == "state":
        invalid_output["business_status"] = "NEEDS_INPUT"
    env.adapter.responses.append(invalid_output)
    _, turn = await generate(env, proposal())
    assert len(env.adapter.calls) == 2
    assert turn.reply.proposal.instructions == proposal()["instructions"]
    assert not (await env.skills.list_skills(env.context)).items


@pytest.mark.parametrize("change", ["resource", "configuration"])
async def test_concurrent_edit_conflict_preserves_candidate(runtime_env, change):
    env = runtime_env
    detail = await env.skills.create(env.context, SkillProposal.model_validate(proposal()))
    identifier = detail.skill.skill_id
    receipt, _ = await generate(env, proposal(), skill_id=identifier)
    if change == "resource":
        await env.skills.edit(
            env.context,
            identifier,
            SkillEdit(
                revision=1,
                name="人工修改名称",
                description=detail.skill.description,
                owner=detail.skill.owner,
                status="ACTIVE",
            ),
        )
    else:
        view = await env.skills.version(env.context, identifier)
        await env.skills.edit_version(
            env.context, identifier, SkillVersionEdit(revision=1, settings=view.settings)
        )
    with pytest.raises(ServiceError) as error:
        await env.runtime.skill_assistance.apply(env.context, receipt.run_id)
    assert error.value.code == "REVISION_CONFLICT"
    assert (await env.runtime.skill_assistance.turn(env.context, receipt.run_id)).reply.proposal


async def test_permission_rechecked_and_agent_management_not_required(runtime_env):
    env = runtime_env
    await revoke(env, "agent:manage")
    receipt, _ = await generate(env, proposal())
    await revoke(env, "version:edit")
    with pytest.raises(ServiceError) as error:
        await env.runtime.skill_assistance.apply(env.context, receipt.run_id)
    assert error.value.code == "FORBIDDEN"
    assert not (await env.skills.list_skills(env.context)).items


async def test_failed_save_rolls_back_resource_configuration_and_receipt(runtime_env, monkeypatch):
    env = runtime_env
    receipt, _ = await generate(env, proposal())
    original = PackageWrite.save_in

    async def interrupted(self, *args, **kwargs):
        await original(self, *args, **kwargs)
        raise ServiceError("SAVE_INTERRUPTED", "模拟写入后异常", 409)

    monkeypatch.setattr(PackageWrite, "save_in", interrupted)
    with pytest.raises(ServiceError, match="模拟"):
        await env.runtime.skill_assistance.apply(env.context, receipt.run_id)
    assert not (await env.skills.list_skills(env.context)).items
    monkeypatch.setattr(PackageWrite, "save_in", original)
    saved = await env.runtime.skill_assistance.apply(env.context, receipt.run_id)
    assert (await env.skills.version(env.context, saved.skill_id)).revision == 1


async def test_foreign_owner_wrong_run_kind_and_client_proposal_rejected(runtime_env):
    env = runtime_env
    receipt, _ = await generate(env, proposal())
    forged = env.context.model_copy(update={"principal_id": "someone_else"})
    with pytest.raises(ServiceError) as error:
        await env.runtime.skill_assistance.turn(forged, receipt.run_id)
    assert error.value.status in {401, 403}
    injected = await env.client.post(
        f"/admin/v1/skills/assistance/runs/{receipt.run_id}/apply", json={"proposal": proposal()}
    )
    assert injected.status_code == 422
    agent_run, _ = await generate_agent(env, key="different-kind")
    with pytest.raises(ServiceError, match="不是技能"):
        await env.runtime.skill_assistance.apply(env.context, agent_run.run_id)


async def test_published_skill_edit_requires_publication_permission(runtime_env):
    env = runtime_env
    detail = await env.skills.create(env.context, SkillProposal.model_validate(proposal()))
    identifier = detail.skill.skill_id
    resources = ResourceManagement(
        env.engine, env.iam.authorization, {"skill": SkillVersionValidator(env.skills)}
    )
    await resources.mutate(
        env.context,
        "skill",
        identifier,
        "publish",
        ResourceMutation(revision=1, configuration_revision=1),
    )
    receipt, _ = await generate(
        env, {**proposal(), "instructions": "已修改的执行步骤。"}, skill_id=identifier
    )
    await env.runtime.skill_assistance.apply(env.context, receipt.run_id)
    assert (await env.skills.version(env.context, identifier)).status.value == "PUBLISHED"
    assert (
        await env.skills.version(env.context, identifier)
    ).instruction_preview == "已修改的执行步骤。"
    await revoke(env, "release:publish")
    with pytest.raises(ServiceError) as error:
        await env.runtime.skill_assistance.submit(
            env.context,
            SkillAssistanceRequest(
                message="继续修改", idempotency_key="revoked", skill_id=identifier
            ),
        )
    assert error.value.code == "FORBIDDEN"


async def test_deleted_saved_skill_is_not_recreated(runtime_env):
    env = runtime_env
    receipt, _ = await generate(env, proposal())
    saved = await env.runtime.skill_assistance.apply(env.context, receipt.run_id)
    await env.skills.deletion.mark(env.context, ContentRef("skill", saved.skill_id), "user_request")
    with pytest.raises(ServiceError) as error:
        await env.runtime.skill_assistance.apply(env.context, receipt.run_id)
    assert error.value.code == "CONTENT_DELETED"


async def test_skill_http_admission_retry_and_failed_generation(runtime_env):
    env = runtime_env
    options = await env.client.get("/admin/v1/skills/assistance/options")
    assert options.status_code == 200 and options.json()[0]["route_id"] == env.route_id
    invalid = proposal()
    invalid["instructions"] += "{{unknown}}"
    env.adapter.responses.extend([output(invalid), output(invalid)])
    body = {"message": "生成技能书", "idempotency_key": "http-skill"}
    receipt = await env.client.post("/admin/v1/skills/assistance", json=body)
    assert receipt.status_code == 202, receipt.text
    repeated = await env.client.post("/admin/v1/skills/assistance", json=body)
    assert repeated.status_code == 202 and repeated.json()["run_id"] == receipt.json()["run_id"]
    injected = await env.client.post(
        "/admin/v1/skills/assistance", json={**body, "channel_id": "foreign"}
    )
    assert injected.status_code == 422
    run_id = receipt.json()["run_id"]
    await execute_message(
        env.runs,
        TaskEnvelope(channel_id=env.context.scope.channel_id, run_id=run_id),
        "http-worker",
        env.runtime,
    )
    turn = await env.client.get(f"/admin/v1/skills/assistance/runs/{run_id}")
    assert turn.status_code == 200 and turn.json()["state"] == "FAILED"
    assert turn.json()["reply"] is None
    assert len(env.adapter.calls) == 2
    assert (
        await env.client.post(f"/admin/v1/skills/assistance/runs/{run_id}/apply")
    ).status_code == 409
    foreign = env.context.model_copy(
        update={"scope": env.context.scope.model_copy(update={"channel_id": "foreign"})}
    )
    with pytest.raises(ServiceError):
        await env.runtime.skill_assistance.turn(foreign, run_id)
    assert not (await env.skills.list_skills(env.context)).items


async def test_skill_catalog_binds_authorized_tool_and_rechecks_changes(runtime_env):
    from creativity_service.modules.tools.schemas import (
        ToolCreate,
        ToolDefinition,
        ToolRelease,
        ToolVersionCreate,
        ToolVersionEdit,
    )

    env = runtime_env
    tool = await env.tools.management.create(
        env.context,
        ToolCreate(
            tool_code="sum",
            name="精确合计",
            description="合计",
            source_type="builtin",
        ),
    )
    definition = ToolDefinition(
        input_schema={"type": "object", "properties": {}, "additionalProperties": False},
        output_schema={"type": "object"},
        model_fields_allowed=(),
        binding={"adapter_key": "decimal_sum", "implementation_version": "1"},
        effect_type="READ_ONLY",
        environments=("test",),
        subject_requirements={"required": False},
    )
    current = await env.tools.management.create_version(
        env.context,
        tool.tool_id,
        ToolVersionCreate(version_label="当前配置", definition=definition),
    )
    frozen = await env.tools.management.freeze(env.context, tool.tool_id, current.revision)
    await env.tools.management.release(
        env.context, tool.tool_id, ToolRelease(version_id=frozen.version.version_id)
    )
    value = proposal()
    value["settings"].update(
        tool_requirements=[{"tool_code": "sum", "source_type": "builtin"}],
        tool_bindings={"sum": tool.tool_id},
        required_model_capabilities=["text"],
    )
    receipt, _ = await generate(env, value)
    model_input = json.loads(env.adapter.calls[0][1].messages[1]["content"])
    assert model_input["tools"][0]["name"] == "精确合计"
    saved = await env.runtime.skill_assistance.apply(env.context, receipt.run_id)
    assert (await env.skills.version(env.context, saved.skill_id)).settings.tool_bindings == {
        "sum": tool.tool_id
    }
    pending, _ = await generate(env, {**value, "skill_code": "another_skill"}, key="stale-tool")
    config = await env.tools.management.version_detail(env.context, tool.tool_id)
    await env.tools.management.edit_version(
        env.context,
        tool.tool_id,
        ToolVersionEdit(
            revision=config.revision,
            definition=definition.model_copy(update={"timeout_seconds": 20}),
        ),
    )
    with pytest.raises(ServiceError) as error:
        await env.runtime.skill_assistance.apply(env.context, pending.run_id)
    assert error.value.code == "REVISION_CONFLICT"
