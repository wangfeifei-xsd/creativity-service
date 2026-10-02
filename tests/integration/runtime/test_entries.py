"""模块首次发布前的调试、评测和会话均经过正式运行执行器。"""

import pytest

from creativity_service.core.context import TaskEnvelope
from creativity_service.core.primitives import ServiceError
from creativity_service.modules.agents.schemas import AgentBindings
from creativity_service.modules.conversations.schemas import ConversationCreate, MessageInput
from creativity_service.modules.prompts.debug import PromptDebugService
from creativity_service.modules.prompts.schemas import PromptSampleCreate, PromptTestRequest
from creativity_service.modules.runtime.admission import RuntimeAdmission
from creativity_service.modules.skills.schemas import SkillCreate, SkillTestInput
from creativity_service.modules.tools.schemas import (
    ToolCreate,
    ToolDefinition,
    ToolTestInput,
    ToolVersionCreate,
)
from creativity_service.workers.executor import execute_message
from tests.integration.agents.test_agents import publish
from tests.integration.runtime.test_execution import admitted, pytestmark

__all__ = ["pytestmark"]


async def execute(env, run_id):
    message = TaskEnvelope(channel_id=env.context.scope.channel_id, run_id=run_id)
    await execute_message(env.runs, message, "worker", env.runtime)
    value = await env.runs.get_run(env.context, run_id)
    assert value.state == "SUCCEEDED", value.error
    assert (await env.runs.load(message))["purpose"] == "debug"
    return value


async def create_tool(env):
    service = env.tools.management
    tool = await service.create(
        env.context,
        ToolCreate(
            tool_code="sum",
            name="精确求和",
            description="计算合计",
            owner="测试人员",
            source_type="builtin",
        ),
    )
    definition = ToolDefinition(
        input_schema={
            "type": "object",
            "properties": {"values": {"type": "array", "items": {"type": "string"}}},
            "required": ["values"],
            "additionalProperties": False,
        },
        output_schema={
            "type": "object",
            "properties": {"sum": {"type": "string"}},
            "required": ["sum"],
            "additionalProperties": False,
        },
        model_fields_allowed=("values",),
        binding={"adapter_key": "decimal_sum", "implementation_version": "1"},
        effect_type="READ_ONLY",
        allowed_data_domains=(env.context.scope.data_scope_id,),
        environments=("test",),
        subject_requirements={"required": False},
    )
    return (
        await service.create_version(
            env.context,
            tool.tool_id,
            ToolVersionCreate(version_label="求和初版", definition=definition),
        )
    ).version


async def test_tool_debug_saves_evidence_and_can_rerun(runtime_env):
    env = runtime_env
    version = await create_tool(env)
    receipt = await env.tools.management.test(
        env.context,
        version.version_id,
        ToolTestInput(revision=version.draft_revision, arguments={"values": ["1.20", "2.30"]}),
    )
    value = await execute(env, receipt.run_id)
    assert value.result.data["data"] == {"sum": "3.50"}
    detail = await env.runs.detail(env.context, receipt.run_id)
    assert detail["evidence"][0]["title"] == "工具调用结果"
    rerun = await env.runs.rerun(env.context, receipt.run_id, "repeat_tool")
    await execute(env, rerun.run_id)
    same = await env.runs.rerun(env.context, receipt.run_id, "repeat_tool")
    assert same.run_id == rerun.run_id


async def test_prompt_sample_creates_metered_debug_run(runtime_env):
    env = runtime_env
    version = await env.prompts.read_version(env.context, env.definition.bindings.prompt_version)
    sample = await env.prompts.create_sample(
        env.context,
        version.resource_id,
        PromptSampleCreate(title="基本验证", input={}, expected_constraints=["包含：验证完成"]),
    )
    debug = PromptDebugService(env.prompts)
    test = await debug.start(
        env.context,
        version.version_id,
        PromptTestRequest(
            revision=version.draft_revision or 2,
            model_route_version=env.definition.bindings.model_route_version,
            sample_id=sample.sample_id,
        ),
    )
    assert test.run_id
    value = await execute(env, test.run_id)
    assert value.result.data["constraints_passed"] is True
    assert value.usage_summary["attempt_count"] == 1


async def test_skill_loading_without_agent_records_files_in_debug(runtime_env):
    env = runtime_env
    skill = await env.skills.create(
        env.context,
        SkillCreate(
            skill_code="summary",
            name="资料整理",
            description="按资料回答",
            owner="测试人员",
            instructions="仅整理输入中的信息。",
        ),
    )
    version = skill.versions[0]
    test = await env.skills.test(
        env.context, version.version_id, SkillTestInput(revision=version.revision)
    )
    assert test.run_id
    value = await execute(env, test.run_id)
    assert value.result.data["complete"] is True
    assert value.result.data["loaded"][0]["path"] == "SKILL.md"
    assert value.usage_summary["attempt_count"] == 0


async def test_tool_loop_stops_at_frozen_call_limit(runtime_env):
    env = runtime_env
    tool = await create_tool(env)
    env.adapter.tool_arguments = '{"values":["1","2"]}'
    definition = env.definition.model_copy(
        update={
            "entrypoint": "tool_loop.v1",
            "workflow_type": "tool_loop",
            "bindings": AgentBindings(
                prompt_version=env.definition.bindings.prompt_version,
                model_route_version=env.definition.bindings.model_route_version,
                tool_versions=(tool.version_id,),
            ),
            "limits": env.definition.limits.model_copy(update={"max_tool_calls": 2}),
        }
    )
    receipt, message = await admitted(env, definition=definition)
    await execute_message(env.runs, message, "worker", env.runtime)
    value = await env.runs.get_run(env.context, receipt.run_id)
    assert value.state == "FAILED" and value.error.code == "CALL_LIMIT", value.error
    trace = await env.runs.trace(env.context, receipt.run_id)
    assert sum(a["kind"] == "tool" for s in trace.items for a in s["attempts"]) == 2
    assert len(env.adapter.calls) == 3


async def test_evaluation_uses_controlled_frozen_candidate(runtime_env):
    env = runtime_env
    detail = await env.agents.create(env.context, env.body)
    version = detail.versions[0]
    spec = await env.agents.freeze_candidate(
        env.context, version.version_id, version.revision, "evaluation"
    )
    receipt = await RuntimeAdmission(env.runs, env.agents).submit(
        env.context, spec, {"request": "评测样例"}, "evaluation"
    )
    message = TaskEnvelope(channel_id=env.context.scope.channel_id, run_id=receipt.run_id)
    await execute_message(env.runs, message, "worker", env.runtime)
    assert (await env.runs.get_run(env.context, receipt.run_id)).state == "SUCCEEDED"
    assert (await env.runs.load(message))["purpose"] == "evaluation"


async def test_conversation_history_and_no_implicit_conversation(runtime_env):
    env = runtime_env
    definition = env.definition.model_copy(
        update={
            "context": env.definition.context.model_copy(
                update={"conversation_enabled": True, "summary_policy": "recent"}
            )
        }
    )
    detail = await env.agents.create(
        env.context, env.body.model_copy(update={"definition": definition})
    )
    assert not (await env.conversations.list_conversations(env.context)).agents
    await publish(env, detail)
    choices = (await env.conversations.list_conversations(env.context)).agents
    assert [(choice.agent_code, choice.name) for choice in choices] == [
        (env.body.agent_code, env.body.name)
    ]
    conversation = await env.conversations.create(
        env.context, ConversationCreate(agent_code=env.body.agent_code, title="业务咨询")
    )
    first = await env.conversations.submit(
        env.context,
        conversation.conversation_id,
        MessageInput(client_message_id="first", content="请处理", input={"request": "请处理"}),
    )
    with pytest.raises(ServiceError, match="会话正在处理"):
        await env.conversations.submit(
            env.context,
            conversation.conversation_id,
            MessageInput(client_message_id="second", content="继续", input={"request": "继续"}),
        )
    message = TaskEnvelope(channel_id=env.context.scope.channel_id, run_id=first.run.run_id)
    await execute_message(env.runs, message, "worker", env.runtime)
    value = await env.runs.get_run(env.context, first.run.run_id)
    assert value.state == "SUCCEEDED", value.error
    second = await env.conversations.submit(
        env.context,
        conversation.conversation_id,
        MessageInput(client_message_id="second", content="继续", input={"request": "继续"}),
    )
    await execute_message(
        env.runs, message.model_copy(update={"run_id": second.run.run_id}), "worker", env.runtime
    )
    assert any(m["role"] == "assistant" for m in env.adapter.calls[-1][1].messages)


@pytest.mark.parametrize("partial", [False, True])
async def test_registered_compute_retry_and_schema_checked_partial(runtime_env, partial):
    env = runtime_env
    step = env.definition.steps[0].model_copy(
        update={
            "kind": "compute",
            "failure_policy": "partial" if partial else "retry",
            "max_retries": 0 if partial else 2,
        }
    )
    definition = env.definition.model_copy(
        update={"workflow_type": "stateful", "entrypoint": "stateful.v1", "steps": (step,)}
    )
    count = 0

    async def compute(context, values):
        nonlocal count
        count += 1
        if partial or count == 1:
            raise ServiceError("SOURCE_UNAVAILABLE", "资料暂不可用", 503)
        return {
            "schema_version": "1.0",
            "business_status": "COMPLETED",
            "data": values,
            "warnings": [],
            "evidence_refs": [],
        }

    env.runtime.registry.register("stateful.v1", "answer", compute)
    receipt, message = await admitted(env, definition=definition)
    await execute_message(env.runs, message, "worker", env.runtime)
    value = await env.runs.get_run(env.context, receipt.run_id)
    assert value.state == "SUCCEEDED", value.error
    assert value.result.business_status == ("PARTIAL" if partial else "COMPLETED")
    assert count == (1 if partial else 2)
    assert not env.adapter.calls
    assert (await env.runs.load(message))["conversation_id"] is None


async def test_result_artifact_is_scoped_and_download_reauthorizes(runtime_env):
    from creativity_service.core.artifacts import ArtifactService
    from creativity_service.core.deletion import ContentRef

    env = runtime_env
    receipt, message = await admitted(env)
    artifacts = ArtifactService(
        env.engine, env.conversations.artifacts.store, env.iam.authorization
    )
    artifact = await artifacts.upload(
        env.context,
        "执行报告.txt",
        "text/plain",
        "报告内容".encode(),
        [ContentRef("run", receipt.run_id)],
    )
    env.adapter.responses = [
        {
            "business_status": "COMPLETED",
            "schema_version": "1.0",
            "data": {"artifact_id": artifact.artifact_id},
            "warnings": [],
            "evidence_refs": [],
        }
    ]
    await execute_message(env.runs, message, "worker", env.runtime)
    value = await env.runs.get_run(env.context, receipt.run_id)
    assert value.state == "SUCCEEDED", value.error
    assert [a.name for a in value.artifacts] == ["执行报告.txt"]
    assert (await artifacts.download(env.context, artifact.artifact_id))[0] == "报告内容".encode()
    wrong = env.context.model_copy(
        update={"scope": env.context.scope.model_copy(update={"channel_id": "another_channel"})}
    )
    with pytest.raises(ServiceError):
        await artifacts.download(wrong, artifact.artifact_id)


async def test_memory_omission_is_reported_in_business_result(runtime_env, monkeypatch):
    from creativity_service.core.deletion import RecoveryService
    from creativity_service.modules.memory.schemas import MemoryLoad, MemoryPolicy, MemorySelection

    env = runtime_env
    env.context = env.context.model_copy(
        update={
            "scope": env.context.scope.model_copy(
                update={"subject_type": "user", "subject_id": "memory-subject"}
            )
        }
    )
    await RecoveryService(env.engine, env.iam.authorization).initialize_fresh(env.context)
    warning = "长期记忆暂不可用，本次未使用记忆"

    async def select(*args, **kwargs):
        return MemorySelection(retrieval_id="unavailable", refs=[], warnings=[warning])

    async def load(*args, **kwargs):
        return MemoryLoad(items=[], warnings=[warning], required_tool_keys=[])

    monkeypatch.setattr(env.memory, "select", select)
    monkeypatch.setattr(env.memory, "load", load)
    definition = env.definition.model_copy(
        update={
            "context": env.definition.context.model_copy(update={"memory_policy": MemoryPolicy()})
        }
    )
    receipt, message = await admitted(env, definition=definition)
    await execute_message(env.runs, message, "worker", env.runtime)
    result = await env.runs.get_run(env.context, receipt.run_id)
    assert result.state == "SUCCEEDED", result.error
    assert result.result.warnings == (warning,)
