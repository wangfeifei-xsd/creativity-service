"""外部已写入但响应丢失、未知状态多次核查与明确未执行后的同键重试。"""

import pytest

from creativity_service.core.context import TaskEnvelope
from creativity_service.core.primitives import utcnow
from creativity_service.integrations.tools import (
    AdapterRegistration,
    AdapterResult,
    ToolAdapterError,
)
from creativity_service.modules.runs.interruptions import ResumeInput
from creativity_service.modules.tools.schemas import (
    ToolCreate,
    ToolDefinition,
    ToolTestInput,
    ToolVersionCreate,
)
from creativity_service.workers.executor import execute_message
from tests.integration.runtime.test_interruptions import pytestmark

__all__ = ["pytestmark"]


class Source:
    def __init__(self, outcome):
        self.outcome, self.writes, self.queries = outcome, [], []

    async def invoke(self, request):
        if request.operation:
            self.writes.append(request.operation["idempotency_key"])
            if len(self.writes) == 1:
                raise ToolAdapterError("TOOL_TIMEOUT", "来源响应丢失", retryable=True)
            data = {"saved": True}
        else:
            self.queries.append(request.arguments["operation_key"])
            data = {
                "operation_key": request.arguments["operation_key"],
                "outcome": "SUCCEEDED" if self.outcome == "MALFORMED" else self.outcome,
                "result": {
                    "data": {"saved": True},
                    "source_request_id": "source-receipt",
                    "source_version": "1",
                    "observed_at": utcnow().isoformat(),
                },
            }
        if not request.operation and self.outcome == "MALFORMED":
            data["result"] = {"data": {"saved": True}}
        return AdapterResult(
            data=data, source_request_id="source-receipt", source_version="1", observed_at=utcnow()
        )


async def prepare(env, source):
    versions = []
    for name, effect, fields in (
        ("check", "READ_ONLY", {"operation_key": {"type": "string"}}),
        ("save", "IDEMPOTENT_WRITE", {"value": {"type": "string"}}),
    ):
        env.tools.registry.register(
            AdapterRegistration(
                key=name,
                name="来源状态" if name == "check" else "保存数据",
                source_type="builtin",
                implementation_version="1",
                actual_effect=effect,
                adapter=source,
                sensitive=False,
            )
        )
        tool = await env.tools.management.create(
            env.context,
            ToolCreate(
                tool_code=name,
                name="来源状态" if name == "check" else "保存数据",
                description="写入核查契约夹具",
                owner="验收",
                source_type="builtin",
            ),
        )
        definition = ToolDefinition(
            input_schema={
                "type": "object",
                "properties": fields,
                "required": list(fields),
                "additionalProperties": False,
            },
            output_schema={"type": "object"},
            model_fields_allowed=tuple(fields),
            binding={"adapter_key": name, "implementation_version": "1"},
            effect_type=effect,
            allowed_data_domains=(env.context.scope.data_scope_id,),
            environments=("test",),
            subject_requirements={"required": False},
            idempotency_policy="none" if name == "check" else "source_key",
            write_policy=None
            if name == "check"
            else {
                "status_tool_version_id": versions[0].version_id,
                "max_submissions": 2,
                "max_checks": 3,
            },
        )
        version = await env.tools.management.create_version(
            env.context,
            tool.tool_id,
            ToolVersionCreate(version_label="验收版", definition=definition),
        )
        versions.append(version.version)
    receipt = await env.tools.management.test(
        env.context,
        versions[-1].version_id,
        ToolTestInput(revision=versions[-1].draft_revision, arguments={"value": "确认内容"}),
    )
    return receipt, TaskEnvelope(channel_id=env.context.scope.channel_id, run_id=receipt.run_id)


async def resume(env, message, decision):
    value = await env.runs.interruption(env.context, message.run_id)
    assert value is not None
    await env.runs.resume(
        env.context,
        message.run_id,
        ResumeInput(
            interruption_id=value.interruption_id,
            revision=value.revision,
            confirmation_digest=value.confirmation_digest,
            decision=decision,
            idempotency_key=value.interruption_id,
        ),
    )
    await execute_message(env.runs, message, "restarted", env.runtime)


@pytest.mark.parametrize("outcome", ["SUCCEEDED", "UNKNOWN", "NOT_EXECUTED", "MALFORMED"])
async def test_write_confirmation_and_source_resolution(runtime_env, outcome):
    env, source = runtime_env, Source(outcome)
    receipt, message = await prepare(env, source)
    await execute_message(env.runs, message, "worker", env.runtime)
    assert not source.writes
    await resume(env, message, "approve")
    result = await env.runs.get_run(env.context, receipt.run_id)
    assert result.state == "WAITING_INPUT", result.model_dump_json()
    assert len(source.writes) == 1
    await resume(env, message, "verify")
    result = await env.runs.get_run(env.context, receipt.run_id)
    if outcome in {"UNKNOWN", "MALFORMED"}:
        assert result.state == "WAITING_INPUT", result.model_dump_json()
        source.outcome = "SUCCEEDED"
        await resume(env, message, "verify")
    elif outcome == "NOT_EXECUTED":
        assert result.state == "WAITING_APPROVAL", result.model_dump_json()
        await resume(env, message, "approve")
        assert len(source.writes) == 2 and source.writes[0] == source.writes[1]
    result = await env.runs.get_run(env.context, receipt.run_id)
    assert result.state == "SUCCEEDED", result.model_dump_json()
    assert result.result.data["data"] == {"saved": True}
    assert set(source.queries) == set(source.writes)
    assert len(source.writes) == (2 if outcome == "NOT_EXECUTED" else 1)
