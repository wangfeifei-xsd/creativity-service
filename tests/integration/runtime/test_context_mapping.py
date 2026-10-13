"""真实执行验证步骤输入边界、显式提示词和工具循环上下文。"""

import json

import pytest

from creativity_service.modules.agents.schemas import AgentEdge, AgentStep, InputSource
from creativity_service.modules.prompts.schemas import (
    InstructionBlocks,
    MessageTemplate,
    PromptContent,
    PromptDraftEdit,
    PromptVariable,
)
from creativity_service.modules.runs.repositories import rows
from creativity_service.workers.executor import execute_message
from tests.integration.runtime.test_entries import create_tool
from tests.integration.runtime.test_execution import admitted, pytestmark
from tests.support.resources import publish_resource

__all__ = ["pytestmark"]


def prepared_flow(env, data, inputs, *, workflow="template"):
    schema = {
        "type": "object",
        "properties": {key: {"type": "string"} for key in data},
        "required": list(data),
    }
    prepare = AgentStep(
        key="prepare",
        name="准备业务数据",
        kind="compute",
        operator="object",
        inputs={key: InputSource(source="constant", value=value) for key, value in data.items()},
        input_schema=schema,
        output_schema=schema,
    )
    answer = env.definition.steps[0].model_copy(
        update={
            "inputs": inputs,
            "input_schema": {
                "type": "object",
                "properties": {key: {"type": "string"} for key in inputs},
                "required": list(inputs),
                "additionalProperties": False,
            },
        }
    )
    return env.definition.model_copy(
        update={
            "workflow_type": workflow,
            "entrypoint": "stateful.v1" if workflow == "stateful" else "workflow.v1",
            "instructions": "仅处理本步骤明确提供的内容",
            "start_step": prepare.key,
            "steps": (prepare, answer),
            "edges": (
                AgentEdge(source=prepare.key, target=answer.key),
                AgentEdge(source=answer.key, target="END"),
            ),
            "context": env.definition.context.model_copy(update={"context_limit": 1000}),
        }
    )


@pytest.mark.parametrize("workflow", ["template", "stateful"])
@pytest.mark.parametrize("mapped", [True, False])
async def test_unmapped_outputs_do_not_enter_model_or_consume_context(
    runtime_env, workflow, mapped
):
    env = runtime_env
    selected = "本步骤需要的业务事实"
    private = "不应传入模型的上游记录" * 3000
    definition = prepared_flow(
        env,
        {"selected": selected, "private": private},
        {"request": InputSource(source="step", step="prepare", path="selected")} if mapped else {},
        workflow=workflow,
    )
    receipt, message = await admitted(env, definition=definition)
    await execute_message(env.runs, message, "mapping-worker", env.runtime)
    result = await env.runs.get_run(env.context, receipt.run_id)
    assert result.state == "SUCCEEDED", result.error
    assert len(env.adapter.calls) == 1
    messages = env.adapter.calls[0][1].messages
    assert len(messages) == 2
    assert "仅处理本步骤明确提供的内容" in messages[0]["content"]
    assert json.loads(messages[1]["content"]) == {"input": {"request": selected} if mapped else {}}
    async with env.engine.connect() as connection:
        contents = await rows(
            connection, "run_contents", env.context.scope.channel_id, run_id=receipt.run_id
        )
    assert any(
        item["kind"] == "step_output" and item["payload"].get("private") == private
        for item in contents
    )
    actual = next(item["payload"] for item in contents if item["kind"] == "inputs:answer.attempt0")
    assert actual["request"]["messages"] == list(messages)


async def test_explicit_prompt_tool_variable_still_receives_selected_output(runtime_env):
    env = runtime_env
    tool = await create_tool(env)
    await publish_resource(env, "tool", tool.resource_id)
    tool_definition = (
        await env.tools.management.version_detail(env.context, tool.version_id)
    ).definition
    draft = await env.prompts.version_detail(env.context, env.prompt_id)
    await env.prompts.edit_draft(
        env.context,
        env.prompt_id,
        PromptDraftEdit(
            revision=draft.revision,
            content=PromptContent(
                instruction_blocks=InstructionBlocks(system="根据明确引用的资料回答"),
                message_templates=[MessageTemplate(source="tool", template="{{prepare|json}}")],
                variables=[
                    PromptVariable(
                        name="prepare", display_name="准备结果", type="object", source="tool"
                    )
                ],
            ),
        ),
    )
    data = {"sum": "3"}
    definition = prepared_flow(env, data, {})
    prepare, answer = definition.steps
    prepare = prepare.model_copy(
        update={
            "kind": "tool",
            "operator": None,
            "dependency": tool.version_id,
            "inputs": {"values": InputSource(source="constant", value=["1", "2"])},
            "input_schema": tool_definition.input_schema,
        }
    )
    definition = definition.model_copy(
        update={
            "steps": (prepare, answer),
            "bindings": definition.bindings.model_copy(
                update={"tool_versions": (tool.version_id,)}
            ),
        }
    )
    receipt, message = await admitted(env, definition=definition)
    await execute_message(env.runs, message, "prompt-worker", env.runtime)
    result = await env.runs.get_run(env.context, receipt.run_id)
    assert result.state == "SUCCEEDED", result.error
    messages = env.adapter.calls[0][1].messages
    assert "根据明确引用的资料回答" in messages[0]["content"]
    assert json.loads(messages[1]["content"].split("：\n", 1)[1]) == data
    assert json.loads(messages[-1]["content"]) == {"input": {}}


async def test_tool_dialogue_stays_with_its_model_step(runtime_env):
    env = runtime_env
    tool = await create_tool(env)
    await publish_resource(env, "tool", tool.resource_id)
    env.adapter.tool_arguments = '{"values":["120","3"]}'
    env.adapter.tool_rounds = 1
    first = env.definition.steps[0]
    review = first.model_copy(
        update={
            "key": "review",
            "name": "仅复核最终结果",
            "inputs": {"request": InputSource(source="constant", value="只核对结果")},
        }
    )
    definition = env.definition.model_copy(
        update={
            "workflow_type": "tool_loop",
            "entrypoint": "tool_loop.v1",
            "steps": (first, review),
            "edges": (
                AgentEdge(source=first.key, target=review.key),
                AgentEdge(source=review.key, target="END"),
            ),
            "bindings": env.definition.bindings.model_copy(
                update={"tool_versions": (tool.version_id,)}
            ),
        }
    )
    receipt, message = await admitted(env, definition=definition)
    await execute_message(env.runs, message, "tool-worker", env.runtime)
    result = await env.runs.get_run(env.context, receipt.run_id)
    assert result.state == "SUCCEEDED", result.error
    assert len(env.adapter.calls) == 3
    initial, continued, reviewed = [call[1].messages for call in env.adapter.calls]
    assert [item["role"] for item in initial] == ["system", "user"]
    assert [item["role"] for item in continued] == ["system", "user", "assistant", "tool"]
    assert continued[2]["tool_calls"][0]["id"] == continued[3]["tool_call_id"]
    assert json.loads(continued[3]["content"])["data"] == {"sum": "123"}
    assert [item["role"] for item in reviewed] == ["system", "user"]
    assert json.loads(reviewed[-1]["content"]) == {"input": {"request": "只核对结果"}}
    await execute_message(env.runs, message, "redelivered", env.runtime)
    assert len(env.adapter.calls) == 3
