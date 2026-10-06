"""真实 PostgreSQL 和 IAM，供应商能力仅使用受信完成回调夹具。"""

from types import SimpleNamespace

import pytest

from creativity_service.core.deletion import CleanupRegistry
from creativity_service.core.security.outbound import Destination, OutboundPolicy
from creativity_service.modules.agents.assembly import build_agent_service
from creativity_service.modules.agents.registry import templates
from creativity_service.modules.agents.schemas import AgentBindings, AgentCreate
from creativity_service.modules.budgets.services import BudgetService
from creativity_service.modules.iam.schemas import ChannelContextInput
from creativity_service.modules.models.assembly import build_model_services
from creativity_service.modules.models.schemas import (
    CaseResult,
    ConnectionInput,
    CredentialInput,
    ModelInput,
    ProviderInput,
    RouteInput,
    RouteVersionInput,
)
from creativity_service.modules.models.schemas import (
    TestCompletion as Completion,
)
from creativity_service.modules.models.schemas import (
    TestInput as Cases,
)
from creativity_service.modules.prompts.assembly import build_prompt_service
from creativity_service.modules.prompts.schemas import (
    PromptContent,
    PromptCreate,
    PromptDraftCreate,
)
from creativity_service.modules.skills.assembly import build_skill_service
from creativity_service.modules.tools.assembly import build_tool_services
from tests.integration.channels.conftest import channel_body, login
from tests.integration.channels.conftest import channel_env as channel_env
from tests.integration.channels.test_prompts_http import MemoryStore
from tests.integration.models.test_models import Executor, Keys


@pytest.fixture
async def agent_env(channel_env, request):
    env = channel_env
    parameters = getattr(request, "param", "test")
    environment = parameters["environment"] if isinstance(parameters, dict) else parameters
    independent = (
        parameters.get("independent_actions", ["release:publish"])
        if isinstance(parameters, dict)
        else ["release:publish"]
    )
    channel = await env.services.channels.create(
        env.admin,
        channel_body(env).model_copy(
            update={"environment": environment, "independent_actions": independent}
        ),
    )
    _, admin = await login(env)
    token = await env.iam.sessions.enter(
        admin,
        ChannelContextInput(
            channel_id=channel.channel_id,
            environment=environment,
        ),
    )
    manager = await env.iam.authentication.admin_session(
        token.access_token, "agent-fixture", governance=True
    )
    tenant = SimpleNamespace(channel=channel, token=token, manager=manager)

    async def resolve(host, port):
        return ["93.184.216.34"]

    models = build_model_services(
        env.engine,
        env.iam,
        key_provider=Keys(),
        outbound=OutboundPolicy(
            (Destination(channel.channel_id, environment, "model", "models.example"),), resolve
        ),
    )
    provider = await models.configuration.save_provider(
        env.admin, ProviderInput(code="fixture", name="能力夹具", protocols=["chat_completions"])
    )
    credential = await models.configuration.store_credential(
        manager, CredentialInput(secret="test-only-credential")
    )
    connection = await models.configuration.save_connection(
        manager,
        ConnectionInput(
            name="夹具连接",
            provider_id=provider.id,
            protocol="chat_completions",
            endpoint="https://models.example/v1",
            credential_ref=credential,
        ),
    )
    model = await models.configuration.save_model(
        manager,
        ModelInput(
            model_code="fixture",
            name="夹具模型",
            connection_id=connection.id,
            provider_model_name="fixture",
            context_limit=32000,
            parameters={"max_tokens": 100},
        ),
    )
    models.configuration.executor = Executor()
    test = await models.testing.create(
        tenant.manager, model.id, Cases(cases=["text", "schema", "tools", "stream_cancel", "usage"])
    )
    await models.testing.complete(
        tenant.manager.context,
        test.id,
        Completion(
            run_id=test.run_id,
            config_digest=test.config_digest,
            results=[
                CaseResult(case=c, passed=True, attempt_ids=["fixture_" + c])
                for c in ["text", "schema", "tools", "stream_cancel", "usage"]
            ],
            latency_ms=1,
            evidence="live",
        ),
    )
    route = await models.routing.create(
        tenant.manager, RouteInput(code="structured", name="结构化路由")
    )
    route_version = await models.routing.create_version(
        tenant.manager,
        route.id,
        RouteVersionInput(
            label="模型路由初版",
            primary_model=model.id,
            required_capabilities=["text", "structured_output", "tools"],
        ),
    )
    env.context, env.tenant, env.models, env.model = tenant.manager.context, tenant, models, model
    store = MemoryStore()
    env.prompts = build_prompt_service(env.engine, env.iam.authorization, store)
    prompt = await env.prompts.create(
        env.context, PromptCreate(prompt_code="agent_prompt", name="业务提示词", purpose="验证配置")
    )
    draft = await env.prompts.create_draft(
        env.context,
        prompt.prompt_id,
        PromptDraftCreate(version_label="初版", content=PromptContent()),
    )
    # 本单元以已发布提示词为前置夹具；不伪造提示词调试或正式评测结果。
    from creativity_service.core.database import transaction
    from creativity_service.core.deletion import content_key
    from creativity_service.core.locking import record_key
    from creativity_service.core.versioning import version_view
    from creativity_service.modules.agents.repositories import repository

    async with transaction(
        env.engine,
        env.context.scope,
        [
            content_key(env.context.scope),
            record_key(env.context.scope.channel_id, "resource_versions", draft.version.version_id),
        ],
    ) as uow:
        row = await repository("resource_versions", env.context.scope).change(
            uow, draft.version.version_id, draft.revision, {"state": "PUBLISHED"}
        )
        prompt_version = version_view(row)
    env.tools = build_tool_services(env.engine, env.iam.authorization)
    env.skills = build_skill_service(env.engine, env.iam.authorization, store, env.tools.management)
    env.cleanup = CleanupRegistry()
    env.agents = build_agent_service(
        env.engine,
        env.iam.authorization,
        env.tools.management,
        env.skills,
        BudgetService(env.engine),
        cleanup=env.cleanup,
    )
    env.definition = templates()[0].definition.model_copy(
        update={
            "bindings": AgentBindings(
                prompt_version=prompt_version.version_id,
                model_route_version=route_version.version_id,
            )
        }
    )
    env.body = AgentCreate(
        agent_code="structured_task",
        name="业务助手",
        description="处理业务诉求",
        owner="配置人员",
        definition=env.definition,
    )
    env.client._transport.app.state.agents = env.agents
    env.client.headers["Authorization"] = "Bearer " + tenant.token.access_token
    yield env
