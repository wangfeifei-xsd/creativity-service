"""API 与 Worker 共用运行装配，冻结测试和正式任务走同一执行器。"""

import json
from typing import Any

from creativity_service.core.context import AuthContext
from creativity_service.core.database import transaction
from creativity_service.core.deletion import ContentRef, DeletionGuard
from creativity_service.modules.agents.services import AgentService
from creativity_service.modules.conversations.services import ConversationService
from creativity_service.modules.iam.authorization import IamAuthorization
from creativity_service.modules.memory.services import MemoryService
from creativity_service.modules.models.assembly import ModelServices
from creativity_service.modules.prompts.services import PromptService
from creativity_service.modules.runs.services import RunService
from creativity_service.modules.runtime.admission import (
    RuntimeAdmission,
)
from creativity_service.modules.runtime.context import ContextBuilder
from creativity_service.modules.runtime.debug import (
    AgentDebug,
    DebugExecutor,
    ModelDebug,
    PromptDebug,
    SkillDebug,
    ToolDebug,
)
from creativity_service.modules.runtime.directory import ConversationAgents
from creativity_service.modules.runtime.engine import RuntimeExecutor
from creativity_service.modules.runtime.model import ModelRunner
from creativity_service.modules.runtime.registry import StepRegistry
from creativity_service.modules.runtime.storage import load_spec
from creativity_service.modules.skills.services import SkillService
from creativity_service.modules.tools.services import ToolService


def install_runtime(
    runs: RunService,
    agents: AgentService,
    models: ModelServices,
    prompts: PromptService,
    skills: SkillService,
    tools: ToolService,
    conversations: ConversationService,
    memory: MemoryService,
    authorization: IamAuthorization,
    *,
    evidence: str = "live",
    registry: StepRegistry | None = None,
) -> RuntimeExecutor:
    from creativity_service.integrations.sandbox import ContainerSandbox
    from creativity_service.integrations.tools.script import resolve_script
    from creativity_service.modules.integrations.automation_assembly import install_automation

    install_automation(runs, authorization)
    sandbox = ContainerSandbox()
    tools.registry.register_resolver(
        lambda scope, binding: resolve_script(skills, sandbox, scope, binding)
    )
    admission = RuntimeAdmission(runs, agents)
    from creativity_service.modules.memory.consolidation import MemoryConsolidation

    runs.memory_consolidation = MemoryConsolidation(memory, runs, admission)
    executor = RuntimeExecutor(
        runs,
        ModelRunner(runs, models),
        ContextBuilder(runs, prompts, skills, conversations, memory),
        tools,
        authorization,
        registry,
    )
    executor.debug = DebugExecutor(executor, evidence=evidence)
    executor.contexts.model_runner = executor.models
    agents.runner = AgentDebug(admission)
    models.configuration.executor = ModelDebug(admission)
    prompt_debug = PromptDebug(admission)
    prompts.runner, prompts.evidence = prompt_debug, prompt_debug
    tools.runs = ToolDebug(admission)
    skills.runtime_runner = SkillDebug(admission)
    conversations.directory = ConversationAgents(agents)

    async def boundary(context: AuthContext, row: dict[str, Any]) -> None:
        if not row["execution_policy"].get("frozen_spec_id"):
            return
        spec = await load_spec(runs, row)
        if spec.scope != context.scope:
            from creativity_service.core.primitives import ServiceError

            raise ServiceError("SCOPE_MISMATCH", "执行快照范围不符", 403)
        if spec.versions[0].resource_type == "agent":
            await agents.check_external_boundary(context, spec)
        for version in spec.versions:
            kind = version.resource_type
            if kind in {"model", "tool", "skill", "prompt"}:
                await authorization.boundary(context, "run:create", kind, version.resource_id)
        async with transaction(runs.engine, context.scope, runs.keys(context, row["id"])) as uow:
            await DeletionGuard(context.scope).check(
                uow, [ContentRef("version", v.version_id) for v in spec.versions]
            )
            if json.loads(spec.payload_json).get("runtime"):
                await runs.snapshot(uow, row)

    runs.boundary = boundary
    runs.runtime_executor = executor
    return executor
