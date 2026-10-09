"""订单协助样例通过通用运行时执行；来源与模型响应均为本机受控夹具。"""

import json
from pathlib import Path

import pytest

from creativity_service.core.context import TaskEnvelope
from creativity_service.core.primitives import RunInput, utcnow
from creativity_service.integrations.tools import AdapterRegistration, AdapterResult
from creativity_service.modules.skills.schemas import SkillCreate, SkillRelease
from creativity_service.modules.tools.schemas import (
    ToolCreate,
    ToolDefinition,
    ToolRelease,
    ToolVersionCreate,
)
from creativity_service.workers.executor import execute_message
from examples.agents.prepare import prepare
from tests.integration.agents.test_agents import publish
from tests.integration.runtime.test_execution import pytestmark

__all__ = ["pytestmark"]
FOLDER = Path("examples/agents/rental-order-guidance")
GUIDANCE = "guidance_1_PAYMENT_GUIDANCE"
CONTEXT = {
    "guidance_id": GUIDANCE,
    "lease_generation": 1,
    "fact_revision": "a" * 64,
    "facts": {"payments": [{"status": "UNKNOWN"}]},
}


class Source:
    def __init__(self, operation, scenario, calls, saved):
        self.operation, self.scenario, self.calls, self.saved = operation, scenario, calls, saved

    async def invoke(self, request):
        self.calls.append(self.operation)
        if self.operation == "list_pending_guidances":
            data = {"has_work": self.scenario != "empty", "items": []}
            if data["has_work"]:
                data["guidance_ids"] = [GUIDANCE]
        elif self.operation == "claim_guidances":
            work = self.scenario != "race"
            data = {
                "has_work": work,
                "needs_analysis": work and self.scenario != "saved",
                "items": [CONTEXT] if work else [],
                "item": CONTEXT if work else {},
            }
            if work:
                data["guidance_ids"] = [GUIDANCE]
        elif self.operation == "save_guidance_results":
            self.saved.extend(request.arguments["results"])
            data = {
                "items": [
                    {
                        "guidance_id": GUIDANCE,
                        "state": "SKIPPED" if self.scenario == "nohelp" else "SAVED",
                    }
                ]
            }
        elif self.operation == "publish_guidances":
            assert self.scenario == "saved" or self.saved
            data = {
                "items": [
                    {
                        "guidance_id": GUIDANCE,
                        "state": "SKIPPED" if self.scenario == "nohelp" else "PUBLISHED",
                    }
                ]
            }
        else:
            raise AssertionError("正常流程不应调用额外工具")
        return AdapterResult(
            data=data, source_request_id=self.operation, source_version="1", observed_at=utcnow()
        )


@pytest.mark.parametrize("scenario", ["empty", "race", "saved", "guide", "nohelp"])
async def test_configured_guidance_routes_skip_unneeded_analysis_and_save_before_publish(
    runtime_env, scenario
):
    env = runtime_env
    template = json.loads((FOLDER / "agent.json").read_text())
    steps = {
        s["dependency"].removeprefix("bind_"): s
        for s in template["definition"]["steps"]
        if s["kind"] == "tool"
    }
    bindings = {
        "bind_prompt": env.definition.bindings.prompt_version,
        "bind_model_route": env.definition.bindings.model_route_version,
    }
    calls, saved = [], []
    for operation in (
        "get_operation_status",
        "get_guidance_contexts",
        "list_pending_guidances",
        "claim_guidances",
        "save_guidance_results",
        "publish_guidances",
    ):
        policy = json.loads((FOLDER / f"{operation}.policy.json").read_text())
        if policy.get("write_policy"):
            policy["write_policy"]["status_tool_version_id"] = bindings["bind_get_operation_status"]
            policy["write_policy"]["allowed_principal_ids"] = [env.context.principal_id]
        schema = steps.get(operation, {}).get("input_schema") or {
            "type": "object",
            "properties": {"operation_key": {"type": "string"}},
            "required": ["operation_key"],
            "additionalProperties": False,
        }
        output = steps.get(operation, {}).get("output_schema", {"type": "object"})
        env.tools.registry.register(
            AdapterRegistration(
                key=operation,
                name="协助工具夹具",
                source_type="builtin",
                implementation_version="1",
                actual_effect=policy["effect_type"],
                adapter=Source(operation, scenario, calls, saved),
                sensitive=False,
            )
        )
        tool = await env.tools.management.create(
            env.context,
            ToolCreate(
                tool_code=operation,
                name="协助工具夹具",
                description="配置工作流验证",
                source_type="builtin",
                owner="测试",
            ),
        )
        draft = await env.tools.management.create_version(
            env.context,
            tool.tool_id,
            ToolVersionCreate(
                definition=ToolDefinition(
                    input_schema=schema,
                    output_schema=output,
                    model_fields_allowed=tuple(schema["properties"]),
                    binding={"adapter_key": operation, "implementation_version": "1"},
                    environments=("test",),
                    **policy,
                )
            ),
        )
        frozen = await env.tools.management.freeze(
            env.context, draft.version.version_id, draft.revision
        )
        await env.tools.management.release(
            env.context, tool.tool_id, ToolRelease(version_id=frozen.version.version_id)
        )
        bindings["bind_" + operation] = frozen.version.version_id
    skill = await env.skills.create(
        env.context,
        SkillCreate(
            skill_code="guidance",
            name="订单协助",
            description="帮助用户理解本单",
            owner="测试",
            instructions=(FOLDER / "SKILL.md").read_text().split("---", 2)[2],
        ),
    )
    version = skill.versions[0]
    bindings["bind_skill"] = (
        await env.skills.freeze(env.context, version.version_id, version.revision)
    ).version_id
    await env.skills.release(
        env.context, skill.skill.skill_id, SkillRelease(version_id=bindings["bind_skill"])
    )
    body = prepare(template, bindings)
    detail = await env.agents.create(env.context, body)
    await publish(env, detail)
    model_result = {
        "guidance_id": GUIDANCE,
        "lease_generation": 1,
        "fact_revision": "a" * 64,
        "assistance_decision": "NO_ADDITIONAL_HELP" if scenario == "nohelp" else "GUIDE",
        "assessment": "核对实际支付结果，避免重复付款",
        "views": [],
    }
    if scenario == "guide":
        model_result["views"].append(
            {
                "role": "TENANT",
                "user_problem": "付款结果待核实",
                "situation": "已有支付尝试，结果尚不确定",
                "next_step": "查看原交易进度",
                "reason": "避免重复付款",
                "steps": ["打开本单支付页"],
                "problem_path": "必要时联系平台核实",
                "action_codes": ["VIEW_ORDER"],
                "evidence_refs": ["order:1"],
            }
        )
    env.adapter.responses = [{"results": [model_result]}]
    receipt = await env.runs.admit_run(
        env.context, RunInput(agent_code=body.agent_code, input={}), "production"
    )
    message = TaskEnvelope(channel_id=env.context.scope.channel_id, run_id=receipt.run_id)
    await execute_message(env.runs, message, "configured", env.runtime)
    result = await env.runs.get_run(env.context, receipt.run_id)
    assert result.state == "SUCCEEDED", result.error
    expected = {
        "empty": ["list_pending_guidances"],
        "race": ["list_pending_guidances", "claim_guidances"],
        "saved": ["list_pending_guidances", "claim_guidances", "publish_guidances"],
    }.get(
        scenario,
        ["list_pending_guidances", "claim_guidances", "save_guidance_results", "publish_guidances"],
    )
    assert calls == expected
    assert len(env.adapter.calls) == (1 if scenario in {"guide", "nohelp"} else 0)
    if saved:
        assert saved == [model_result]
    await execute_message(env.runs, message, "redelivered", env.runtime)
    assert calls == expected
