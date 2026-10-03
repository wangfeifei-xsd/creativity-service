"""独立 Worker 的生产依赖装配，与 API 使用同一模块服务和执行入口。"""

from creativity_service.core.artifacts import S3ObjectStore
from creativity_service.core.infrastructure import Infrastructure
from creativity_service.core.services import build_core_services
from creativity_service.modules.agents.assembly import build_agent_service
from creativity_service.modules.budgets.services import BudgetService
from creativity_service.modules.conversations.assembly import build_conversation_service
from creativity_service.modules.evaluations.assembly import build_evaluation_service
from creativity_service.modules.iam.services import IamServices
from creativity_service.modules.integrations.assembly import build_integration_services
from creativity_service.modules.integrations.delegation import CurrentSubjectReader
from creativity_service.modules.mcp.assembly import build_mcp_service
from creativity_service.modules.memory.assembly import build_memory_service
from creativity_service.modules.models.assembly import build_model_services
from creativity_service.modules.prompts.assembly import build_prompt_service
from creativity_service.modules.runs.assembly import build_run_service
from creativity_service.modules.runs.services import RunService
from creativity_service.modules.runtime.assembly import install_runtime
from creativity_service.modules.runtime.registry import StepRegistry
from creativity_service.modules.skills.assembly import build_skill_service
from creativity_service.modules.tools.assembly import build_tool_services
from creativity_service.modules.usage.services import UsageService


def worker_services(
    infrastructure: Infrastructure,
    iam: IamServices,
    *,
    registry: StepRegistry | None = None,
    current_subjects: CurrentSubjectReader | None = None,
) -> RunService:
    engine = infrastructure.engine
    store = S3ObjectStore(infrastructure.s3, infrastructure.bucket)
    core = build_core_services(engine, store, iam.authorization)
    budgets = BudgetService(engine)
    runs = build_run_service(engine, iam, core.versions, budgets, UsageService(engine, budgets))
    tools = build_tool_services(engine, iam.authorization)
    skills = build_skill_service(engine, iam.authorization, store, tools.management)
    mcp = build_mcp_service(engine, iam.authorization, tools)
    build_integration_services(
        engine, iam.authorization, current_subjects=current_subjects, mcp=mcp
    )
    prompts = build_prompt_service(engine, iam.authorization, store)
    models = build_model_services(engine, iam)
    conversations = build_conversation_service(engine, iam.authorization, runs, store)
    memory = build_memory_service(engine, iam.authorization)
    agents = build_agent_service(engine, iam.authorization, tools.management, skills, budgets)
    install_runtime(
        runs,
        agents,
        models,
        prompts,
        skills,
        tools.management,
        conversations,
        memory,
        iam.authorization,
        registry=registry,
    )
    evaluations = build_evaluation_service(engine, iam.authorization, agents, runs, core.cleanup)
    runs.evaluation_sweep = evaluations.sweep
    return runs
