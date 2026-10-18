"""真实 MySQL、运行服务和 Docker 验证节点脚本；外部查询与模型使用夹具。"""

import copy
import os
from datetime import timedelta
from pathlib import Path

import pytest

from creativity_service.core.database import transaction
from creativity_service.core.primitives import ServiceError, digest, utcnow
from creativity_service.integrations.sandbox import SandboxProfile
from creativity_service.integrations.tools import (
    AdapterRegistration,
    AdapterResult,
    ToolAdapterError,
)
from creativity_service.modules.agents.schemas import (
    AgentBindings,
    AgentCondition,
    AgentEdge,
    AgentStep,
    AgentVersionEdit,
    InputSource,
    NodePython,
    StepPagination,
    pagination_output_schema,
)
from creativity_service.modules.runs.repositories import save
from creativity_service.modules.runtime.storage import load_spec
from creativity_service.modules.tools.schemas import ToolCreate, ToolDefinition, ToolVersionCreate
from creativity_service.workers.executor import execute_message
from tests.integration.runtime.test_execution import admitted, pytestmark
from tests.support.resources import publish_resource

__all__ = ["pytestmark"]
VALIDATION_SCRIPT = Path("examples/agents/node-scripts/validate_input.py").read_text()
PROJECT_SCRIPT = Path("examples/agents/node-scripts/validate_projects.py").read_text()


def configure(env, *, seconds=10):
    image = os.getenv("CREATIVITY_SANDBOX_TEST_IMAGE")
    if not image:
        pytest.fail("节点脚本集成测试需要 CREATIVITY_SANDBOX_TEST_IMAGE 固定摘要镜像")
    profile = SandboxProfile(
        profile_id="nodes",
        name="节点计算环境",
        image=image,
        command=("python3", "-I", "-B"),
        mode="python",
        channels={env.context.scope.channel_id},
        seconds=seconds,
    )
    env.agents.sandbox.settings.profiles = [profile]
    return lambda source: NodePython(
        source=source,
        profile_id=profile.profile_id,
        profile_digest=digest(profile.model_dump(mode="json")),
    )


def script_definition(env, script, *, source=VALIDATION_SCRIPT, request="  请处理  "):
    output = copy.deepcopy(env.definition.output_schema)
    output["properties"]["data"] = {
        "type": "object",
        "properties": {
            "success": {"type": "boolean"},
            "conclusion": {"type": "string"},
            "inputs": {"type": "object", "properties": {"request": {"type": "string"}}},
        },
        "required": ["success", "conclusion", "inputs"],
    }
    step = AgentStep(
        key="validate",
        name="校验并整理参数",
        kind="script",
        inputs={"request": InputSource(source="constant", value=request)},
        input_schema=env.definition.input_schema,
        output_schema=output,
        script=script(source),
        failure_policy="fail",
        max_retries=0,
    )
    return env.definition.model_copy(
        update={
            "workflow_type": "template",
            "entrypoint": "workflow.v1",
            "start_step": step.key,
            "steps": (step,),
            "edges": (AgentEdge(source=step.key, target="END"),),
            "bindings": AgentBindings(),
        }
    )


@pytest.mark.parametrize(
    "success,failure_target", [(True, "END"), (False, "END"), (False, "handle")]
)
async def test_script_result_routes_success_and_business_failure(
    runtime_env, success, failure_target
):
    env = runtime_env
    script = configure(env)
    definition = script_definition(env, script, request="  请处理  " if success else "   ")
    validate = definition.steps[0]
    forward = AgentStep(
        key="forward",
        name="接收校验结果和入参",
        kind="compute",
        operator="object",
        input_schema=env.definition.output_schema,
        output_schema=env.definition.output_schema,
        inputs={
            "business_status": InputSource(source="step", step="validate", path="business_status"),
            "schema_version": InputSource(source="constant", value="1.0"),
            "data": InputSource(source="step", step="validate", path="data"),
            "warnings": InputSource(source="constant", value=[]),
            "evidence_refs": InputSource(source="constant", value=[]),
        },
        failure_policy="fail",
        max_retries=0,
    )
    steps = [validate, forward]
    edges = [
        AgentEdge(
            source="validate",
            target="forward",
            condition=AgentCondition(path="data.success", value=True),
        ),
        AgentEdge(source="validate", target=failure_target, otherwise=True),
        AgentEdge(source="forward", target="END"),
    ]
    if failure_target == "handle":
        steps.append(forward.model_copy(update={"key": "handle", "name": "接收失败结论"}))
        edges.append(AgentEdge(source="handle", target="END"))
    definition = definition.model_copy(update={"steps": tuple(steps), "edges": tuple(edges)})
    receipt, message = await admitted(env, definition=definition)
    await execute_message(env.runs, message, "routing-worker", env.runtime)
    result = await env.runs.get_run(env.context, receipt.run_id)
    assert result.state == "SUCCEEDED", result.error
    assert result.result.data["success"] is success
    assert result.result.data["inputs"] == {"request": "请处理" if success else ""}
    assert result.result.business_status == ("COMPLETED" if success else "NEEDS_INPUT")
    if not success:
        assert "不能为空" in result.result.data["conclusion"]
    detail = await env.runs.detail(env.context, receipt.run_id)
    processed = next(entry for entry in detail["actual_inputs"] if entry["name"] == validate.name)
    assert processed["value"]["processed"]["data"]["success"] is success
    async with env.engine.connect() as connection:
        from creativity_service.modules.runs.repositories import rows

        executed = {
            row["node_key"]
            for row in await rows(
                connection, "run_steps", env.context.scope.channel_id, run_id=receipt.run_id
            )
        }
    assert ("forward" in executed) is success
    assert ("handle" in executed) is (not success and failure_target == "handle")
    assert not env.adapter.calls
    await execute_message(env.runs, message, "redelivery", env.runtime)
    assert (await env.runs.get_run(env.context, receipt.run_id)).result == result.result


async def test_script_source_is_frozen_and_needs_no_model_binding(runtime_env):
    env = runtime_env
    script = configure(env)
    definition = script_definition(env, script)
    receipt, message = await admitted(env, definition=definition)
    frozen = await load_spec(env.runs, await env.runs.load(message))
    changed = definition.steps[0].model_copy(
        update={"script": script('def transform(value, context): raise ValueError("新草稿")')}
    )
    await env.agents.edit_version(
        env.context,
        frozen.source_version_id,
        AgentVersionEdit(
            revision=frozen.source_revision,
            definition=definition.model_copy(update={"steps": (changed,)}),
        ),
    )
    await execute_message(env.runs, message, "frozen-worker", env.runtime)
    result = await env.runs.get_run(env.context, receipt.run_id)
    assert result.state == "SUCCEEDED", result.error
    assert result.result.data["inputs"] == {"request": "请处理"}
    assert not env.adapter.calls


@pytest.mark.parametrize(
    "source,code",
    [
        ('def transform(value, context): raise ValueError("输入不合法")', "NODE_SCRIPT_FAILED"),
        ('def transform(value, context): return {"request": 3}', "NODE_SCRIPT_RESULT_INVALID"),
        ("def transform(value, context): return []", "NODE_SCRIPT_FAILED"),
        ("def transform(value, context):\n    while True: pass", "SANDBOX_TIMEOUT"),
    ],
)
async def test_script_technical_failure_is_not_a_business_result(runtime_env, source, code):
    env = runtime_env
    script = configure(env, seconds=1)
    receipt, message = await admitted(env, definition=script_definition(env, script, source=source))
    await execute_message(env.runs, message, "failure-worker", env.runtime)
    result = await env.runs.get_run(env.context, receipt.run_id)
    assert result.state == "FAILED", result.error
    assert result.error.code == code
    assert result.result is None and not env.adapter.calls
    assert "输入不合法" not in result.error.message
    if code == "NODE_SCRIPT_FAILED":
        detail = await env.runs.detail(env.context, receipt.run_id)
        assert any(item["value"].get("errors") for item in detail["actual_inputs"])


def response(index, more):
    return {
        "tenant_id": "business-one",
        "items": [
            {
                "id": f"project-{index}",
                "specs": [{"cpu_cores": 4, "memory_gb": 8, "price": str(10 - index)}],
            }
        ],
        "count": 1,
        "total": 3,
        "has_more": more,
        "cursor": f"page-{index + 1}" if more else None,
    }


class Pages:
    def __init__(self, pages):
        self.pages = pages
        self.calls = []

    async def invoke(self, request):
        self.calls.append(copy.deepcopy(request.arguments))
        value = self.pages[len(self.calls) - 1]
        if isinstance(value, Exception):
            raise value
        return AdapterResult(
            data=value,
            cursor=value["cursor"],
            has_more=value["has_more"],
            coverage={"total": value["total"]},
            source_request_id=request.attempt_id,
            source_version="fixture",
            observed_at=utcnow(),
        )


async def paged_definition(env, pages, *, iterations=5, calls=5, failure_policy="fail"):
    script = configure(env)
    source = Pages(pages)
    env.tools.management.registry.register(
        AdapterRegistration(
            key="project_pages",
            name="分页夹具",
            source_type="builtin",
            implementation_version="1",
            actual_effect="READ_ONLY",
            adapter=source,
            sensitive=False,
        )
    )
    tool = await env.tools.management.create(
        env.context,
        ToolCreate(
            tool_code="pages", name="项目查询", description="按游标查询项目", source_type="builtin"
        ),
    )
    inputs = {
        "type": "object",
        "properties": {"tenant_id": {"type": "string"}, "cursor": {"type": ["string", "null"]}},
        "required": ["tenant_id"],
        "additionalProperties": False,
    }
    output = {
        "type": "object",
        "properties": {
            "tenant_id": {"type": "string"},
            "items": {"type": "array", "items": {"type": "object"}},
            "count": {"type": "integer"},
            "total": {"type": "integer"},
            "has_more": {"type": "boolean"},
            "cursor": {"type": ["string", "null"]},
        },
        "required": ["tenant_id", "items", "count", "total", "has_more", "cursor"],
    }
    version = await env.tools.management.create_version(
        env.context,
        tool.tool_id,
        ToolVersionCreate(
            definition=ToolDefinition(
                input_schema=inputs,
                output_schema=output,
                model_fields_allowed=("tenant_id", "cursor"),
                binding={"adapter_key": "project_pages", "implementation_version": "1"},
                effect_type="READ_ONLY",
                environments=("test",),
                subject_requirements={"required": False},
            )
        ),
    )
    await publish_resource(env, "tool", tool.tool_id)
    step = AgentStep(
        key="projects",
        name="查询全部项目",
        kind="tool",
        dependency=version.version.version_id,
        inputs={"tenant_id": InputSource(source="constant", value="business-one")},
        input_schema=inputs,
        output_schema=pagination_output_schema(output),
        pagination=StepPagination(count_path="count", total_path="total"),
        failure_policy=failure_policy,
        max_retries=0,
    )
    validate = AgentStep(
        key="validate",
        name="校验项目并选择最低报价",
        kind="script",
        inputs={
            "query": InputSource(source="step", step=step.key),
            "tenant_id": InputSource(source="constant", value="business-one"),
        },
        input_schema={
            "type": "object",
            "properties": {"query": step.output_schema, "tenant_id": {"type": "string"}},
            "required": ["query", "tenant_id"],
        },
        output_schema=env.definition.output_schema,
        script=script(PROJECT_SCRIPT),
        failure_policy="fail",
        max_retries=0,
    )
    definition = env.definition.model_copy(
        update={
            "workflow_type": "template",
            "entrypoint": "workflow.v1",
            "start_step": step.key,
            "steps": (step, validate),
            "edges": (
                AgentEdge(source=step.key, target=validate.key),
                AgentEdge(source=validate.key, target="END"),
            ),
            "bindings": env.definition.bindings.model_copy(
                update={"tool_versions": (version.version.version_id,)}
            ),
            "limits": env.definition.limits.model_copy(
                update={"max_iterations": iterations, "max_tool_calls": calls}
            ),
        }
    )
    return definition, source


@pytest.mark.parametrize("workflow", ["template", "stateful"])
async def test_full_query_reads_all_pages_and_uses_accumulated_output(runtime_env, workflow):
    env = runtime_env
    definition, source = await paged_definition(
        env, [response(0, True), response(1, True), response(2, False)]
    )
    if workflow == "stateful":
        definition = definition.model_copy(
            update={"workflow_type": "stateful", "entrypoint": "stateful.v1"}
        )
    receipt, message = await admitted(env, definition=definition)
    await execute_message(env.runs, message, "paging-worker", env.runtime)
    result = await env.runs.get_run(env.context, receipt.run_id)
    assert result.state == "SUCCEEDED", result.error
    assert result.result.data["complete"] is True
    assert result.result.data["project_ids"] == ["project-0", "project-1", "project-2"]
    assert result.result.data["cheapest"]["price"] == "8"
    assert [call.get("cursor") for call in source.calls] == [None, "page-1", "page-2"]
    await execute_message(env.runs, message, "redelivered", env.runtime)
    assert len(source.calls) == 3


@pytest.mark.parametrize(
    "case,expected",
    [
        ("query", "SOURCE_FAILURE"),
        ("tenant", "BUSINESS_INVALID"),
        ("count", "PAGINATION_INVALID"),
        ("cursor_missing", "TOOL_RESULT_INVALID"),
        ("cursor_repeated", "PAGINATION_INVALID"),
        ("spec", "BUSINESS_INVALID"),
        ("project", "BUSINESS_INVALID"),
        ("total", "PAGINATION_INVALID"),
        ("iterations", "DATA_INCOMPLETE"),
        ("calls", "DATA_INCOMPLETE"),
    ],
)
async def test_query_anomalies_and_limits_never_claim_full_success(runtime_env, case, expected):
    env = runtime_env
    pages = [response(0, True), response(1, True), response(2, False)]
    if case == "query":
        pages[0] = ToolAdapterError("SOURCE_FAILURE", "查询失败")
    if case == "tenant":
        pages[0]["tenant_id"] = "other"
    if case == "count":
        pages[0]["count"] = 2
    if case == "cursor_missing":
        pages[0]["cursor"] = None
    if case == "cursor_repeated":
        pages[1]["cursor"] = pages[0]["cursor"]
    if case == "spec":
        pages[0]["items"][0]["specs"][0]["memory_gb"] = -1
    if case == "project":
        pages[0]["items"][0].pop("id")
    if case == "total":
        pages[-1]["total"] = 5
    definition, _ = await paged_definition(
        env, pages, iterations=1 if case == "iterations" else 5, calls=1 if case == "calls" else 5
    )
    receipt, message = await admitted(env, definition=definition)
    await execute_message(env.runs, message, "paging-failure", env.runtime)
    result = await env.runs.get_run(env.context, receipt.run_id)
    if expected == "BUSINESS_INVALID":
        assert result.state == "SUCCEEDED", result.error
        assert result.result.business_status == "INSUFFICIENT_DATA"
        assert result.result.data["success"] is False and result.result.data["complete"] is False
        assert "cheapest" not in result.result.data
        return
    assert result.state == "FAILED", result.error
    assert result.error.code == expected
    assert "数据不完整" in result.error.message
    assert result.result is None and not env.adapter.calls


async def test_paging_limit_returns_explicit_partial_when_configured(runtime_env):
    env = runtime_env
    definition, _ = await paged_definition(
        env, [response(0, True)], iterations=1, failure_policy="partial"
    )
    receipt, message = await admitted(env, definition=definition)
    await execute_message(env.runs, message, "partial-worker", env.runtime)
    result = await env.runs.get_run(env.context, receipt.run_id)
    assert result.state == "SUCCEEDED", result.error
    assert result.result.business_status == "PARTIAL"
    assert result.result.data["complete"] is False
    assert any("数据不完整" in warning for warning in result.result.warnings)


async def test_environment_revoked_after_admission_blocks_script(runtime_env):
    env = runtime_env
    script = configure(env)
    definition = script_definition(env, script)
    receipt, message = await admitted(env, definition=definition)
    env.agents.sandbox.settings.profiles = []
    await execute_message(env.runs, message, "revoked-worker", env.runtime)
    result = await env.runs.get_run(env.context, receipt.run_id)
    assert result.error.code == "SANDBOX_UNAVAILABLE"
    assert not env.adapter.calls
    with pytest.raises(ServiceError, match="获准"):
        await env.agents.create(
            env.context,
            env.body.model_copy(
                update={
                    "agent_code": "revoked_script",
                    "definition": definition,
                }
            ),
        )


async def test_pagination_deadline_reports_incomplete_without_script_running(runtime_env):
    env = runtime_env
    definition, source = await paged_definition(env, [response(0, True)])
    receipt, message = await admitted(env, definition=definition)
    async with transaction(
        env.engine, env.context.scope, env.runs.keys(env.context, receipt.run_id)
    ) as uow:
        await save(uow, "runs", receipt.run_id, {"deadline": utcnow() - timedelta(seconds=1)})
    await execute_message(env.runs, message, "expired-worker", env.runtime)
    result = await env.runs.get_run(env.context, receipt.run_id)
    assert result.state == "TIMED_OUT"
    assert "数据不完整" in result.error.message
    assert not source.calls


async def test_assistance_generates_and_saves_single_script_node(runtime_env):
    from tests.integration.runtime.test_agent_assistance import generate, proposal

    env = runtime_env
    script = configure(env)
    value = proposal(env)
    config = value["definition"]
    config.update(workflow_type="template", entrypoint="workflow.v1", bindings={})
    config["steps"][0].update(
        kind="script",
        dependency=None,
        prompt_id=None,
        operator=None,
        script=script(VALIDATION_SCRIPT).model_dump(mode="json"),
        failure_policy="fail",
        max_retries=0,
    )
    receipt, turn = await generate(env, value)
    assert turn.reply.proposal.definition.steps[0].script.source == VALIDATION_SCRIPT
    _, _, inputs, _ = await env.runtime.assistance.record(env.context, receipt.run_id)
    assert inputs["python_environments"][0]["profile_id"] == "nodes"
    saved = await env.runtime.assistance.apply(env.context, receipt.run_id)
    detail = await env.agents.detail(env.context, saved.agent_id)
    node = detail.versions[0].definition.steps[0]
    assert node.kind == "script" and node.script.source == VALIDATION_SCRIPT
    assert not detail.versions[0].definition.dependency_ids()


async def test_script_resource_bindings_are_rejected_when_saving(runtime_env):
    env = runtime_env
    definition = script_definition(env, configure(env))
    node = definition.steps[0].model_copy(
        update={"dependency": env.definition.bindings.model_route_version}
    )
    with pytest.raises(ServiceError, match="脚本处理节点不能配置"):
        await env.agents.create(
            env.context,
            env.body.model_copy(
                update={"definition": definition.model_copy(update={"steps": (node,)})}
            ),
        )
