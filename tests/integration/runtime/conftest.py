"""真实数据库、Redis、IAM 与运行服务；模型响应明确使用夹具证据。"""

import json
from dataclasses import replace

import pytest

from creativity_service.core.contracts import UsageEvent
from creativity_service.core.primitives import utcnow
from creativity_service.core.versioning import VersionService
from creativity_service.integrations.models.contracts import ModelEvent
from creativity_service.modules.budgets.services import BudgetService
from creativity_service.modules.conversations.assembly import build_conversation_service
from creativity_service.modules.memory.assembly import build_memory_service
from creativity_service.modules.runs.assembly import build_run_service
from creativity_service.modules.runtime.assembly import install_runtime
from creativity_service.modules.usage.services import UsageService
from tests.integration.agents.conftest import agent_env as agent_env
from tests.integration.channels.conftest import channel_env as channel_env
from tests.integration.channels.test_prompts_http import MemoryStore


class Adapter:
    def __init__(self):
        self.calls = []
        self.failures = []
        self.before = None
        self.responses = []

    async def events(self, context, config, request, attempt, reservation, cancellation, **kwargs):
        self.calls.append((config, request, attempt))
        if self.before:
            await self.before(context, attempt)
        failure = self.failures.pop(0) if self.failures else None
        if failure:
            yield ModelEvent(
                kind="failed",
                attempt_id=attempt.attempt_id,
                error_code=failure,
                message="供应商暂不可用",
                retryable=True,
            )
            return
        result = (
            self.responses.pop(0)
            if self.responses
            else {
                "business_status": "COMPLETED",
                "schema_version": "1.0",
                "data": {"answer": "验证完成"},
                "warnings": [],
                "evidence_refs": [],
            }
        )
        if request.tools:
            yield ModelEvent(
                kind="tool",
                attempt_id=attempt.attempt_id,
                tool_index=0,
                tool_call_id="call_1",
                tool_name=request.tools[0]["function"]["name"],
                arguments_delta=getattr(self, "tool_arguments", '{"text":"验证完成"}'),
            )
        else:
            yield ModelEvent(
                kind="text",
                attempt_id=attempt.attempt_id,
                text=json.dumps(result, ensure_ascii=False),
            )
            if request.output_schema:
                yield ModelEvent(
                    kind="structured", attempt_id=attempt.attempt_id, structured=result
                )
        yield ModelEvent(
            kind="usage",
            attempt_id=attempt.attempt_id,
            usage=UsageEvent(
                scope=context.scope,
                attempt_id=attempt.attempt_id,
                connection_id=config.connection_id,
                source_request_id=attempt.attempt_id,
                event_version=1,
                status="REPORTED",
                raw_usage={"prompt_tokens": 10, "completion_tokens": 20},
                normalized_tokens={"input": 10, "output": 20},
                subset_relations={},
                cumulative=True,
                final=True,
                observed_at=utcnow(),
            ),
        )
        yield ModelEvent(kind="completed", attempt_id=attempt.attempt_id)


@pytest.fixture
async def runtime_env(agent_env):
    env = agent_env
    budgets = BudgetService(env.engine)
    env.runs = build_run_service(
        env.engine,
        env.iam,
        VersionService(env.engine, env.iam.authorization),
        budgets,
        UsageService(env.engine, budgets),
        cleanup=env.cleanup,
    )
    env.conversations = build_conversation_service(
        env.engine, env.iam.authorization, env.runs, MemoryStore()
    )
    env.memory = build_memory_service(env.engine, env.iam.authorization)
    env.adapter = Adapter()
    env.models = replace(env.models, adapter=env.adapter)
    env.runtime = install_runtime(
        env.runs,
        env.agents,
        env.models,
        env.prompts,
        env.skills,
        env.tools.management,
        env.conversations,
        env.memory,
        env.iam.authorization,
        evidence="fixture",
    )
    app = env.client._transport.app
    app.state.runs = env.runs
    app.state.conversations = env.conversations
    app.state.runtime = env.runtime
    app.state.sync_wait_seconds = 0.02
    app.state.sse_heartbeat_seconds = 0.02
    app.state.sse_poll_seconds = 0.01
    return env
