"""23 的测试进程装配；生产服务共用正式模块，模型和对象存储明确使用替身。"""

import asyncio
import json
import os
import re
import shutil
import socket
from contextlib import asynccontextmanager
from dataclasses import replace
from datetime import timedelta

import httpx
import uvicorn
from sqlalchemy import text

from creativity_service.core.context import TaskEnvelope
from creativity_service.core.security.outbound import Destination, OutboundPolicy
from creativity_service.core.services import build_core_services
from creativity_service.modules.agents.assembly import build_agent_service
from creativity_service.modules.agents.registry import templates
from creativity_service.modules.agents.schemas import AgentBindings
from creativity_service.modules.conversations.assembly import build_conversation_service
from creativity_service.modules.integrations.assembly import build_integration_services
from creativity_service.modules.mcp.assembly import build_mcp_service
from creativity_service.modules.memory.assembly import build_memory_service
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
from creativity_service.modules.models.schemas import TestCompletion as Completion
from creativity_service.modules.models.schemas import TestInput as Cases
from creativity_service.modules.prompts.assembly import build_prompt_service
from creativity_service.modules.runs.assembly import build_run_service
from creativity_service.modules.runtime.assembly import install_runtime
from creativity_service.modules.skills.assembly import build_skill_service
from creativity_service.modules.tools.assembly import build_tool_services
from creativity_service.modules.usage.assembly import build_usage_services
from creativity_service.workers.executor import execute_message
from tests.integration.channels.test_prompts_http import MemoryStore
from tests.integration.integrations.conftest import TestKeys
from tests.integration.models.test_models import Executor
from tests.integration.runtime.conftest import Adapter
from tests.integration.runtime.test_configuration import configure_prompt
from tests.support.independence_evidence import ROOT, WEB


def node_path():
    bundled = ROOT.parent / ".tools/js/node_modules/node/bin/node"
    return os.environ.get("CREATIVITY_TEST_NODE") or (
        str(bundled) if bundled.exists() else shutil.which("node")
    )


def free_port():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


async def browser(env, stage, scenario, **values):
    from creativity_service.core.primitives import utcnow

    process = await asyncio.create_subprocess_exec(
        node_path(),
        str(WEB / "tests/support/business-independence.mjs"),
        cwd=WEB,
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    payload = {
        "stage": stage,
        "scenario": scenario,
        "api": env.api_url,
        "web": env.web_url,
        "expires": (utcnow() + timedelta(days=1)).strftime("%Y-%m-%dT%H:%M"),
        "sessionToken": env.browser_sessions.get(scenario["code"]),
        **values,
    }
    try:
        async with asyncio.timeout(150):
            out, error = await process.communicate(json.dumps(payload).encode())
        diagnostic = re.sub(
            r"(?i)(authorization: Bearer )[A-Za-z0-9_+=/.-]+", r"\1[已脱敏]", error.decode()
        )
        assert process.returncode == 0, diagnostic
        result = json.loads(out)
        env.browser_sessions[scenario["code"]] = result.pop("sessionToken")
        return result
    finally:
        if process.returncode is None:
            process.kill()
            await process.wait()


async def model_config(env, tenant):
    env.context, env.tenant = tenant.manager.context, tenant
    credential = await env.models.configuration.store_credential(
        tenant.manager, CredentialInput(secret="controlled-model-credential")
    )
    connection = await env.models.configuration.save_connection(
        tenant.manager,
        ConnectionInput(
            name="受控模型连接",
            provider_id=env.provider.id,
            protocol="chat_completions",
            endpoint="https://models.example/v1",
            credential_ref=credential,
        ),
    )
    env.model = await env.models.configuration.save_model(
        tenant.manager,
        ModelInput(
            model_code="controlled",
            name="受控模型",
            connection_id=connection.id,
            provider_model_name="controlled",
            context_limit=32000,
            parameters={"max_tokens": 100},
        ),
    )
    env.models.configuration.executor = Executor()
    cases = ["text", "schema", "tools", "stream_cancel", "usage"]
    test = await env.models.testing.create(tenant.manager, env.model.id, Cases(cases=cases))
    # 沿用模型模块能力夹具；此回调不声称经过外部供应商验证，不提供生产评测报告。
    await env.models.testing.complete(
        env.context,
        test.id,
        Completion(
            run_id=test.run_id,
            config_digest=test.config_digest,
            results=[
                CaseResult(case=c, passed=True, attempt_ids=["fixture_23_" + c]) for c in cases
            ],
            latency_ms=1,
            evidence="live",
        ),
    )
    route = await env.models.routing.create(
        tenant.manager, RouteInput(code="controlled_route", name="验证模型路由")
    )
    version = await env.models.routing.create_version(
        tenant.manager,
        route.id,
        RouteVersionInput(
            label="受控初版",
            primary_model=env.model.id,
            required_capabilities=["text", "structured_output", "tools", "streaming"],
        ),
    )
    env.definition = templates()[0].definition.model_copy(
        update={"bindings": AgentBindings(model_route_version=version.version_id)}
    )
    env.client.headers["Authorization"] = "Bearer " + tenant.token
    env.adapter.before = None
    await configure_prompt(env, "text-brief")


@asynccontextmanager
async def runtime(env, folder):
    app = env.client._transport.app
    store = MemoryStore()
    core = build_core_services(env.engine, store, env.iam.authorization)
    env.outbound = OutboundPolicy(())

    async def resolve(host, port):
        return ["93.184.216.34"]

    env.model_resolver = resolve
    env.tools = build_tool_services(env.engine, env.iam.authorization)
    env.mcp = build_mcp_service(env.engine, env.iam.authorization, env.tools, outbound=env.outbound)
    env.bundle = build_integration_services(
        env.engine, env.iam.authorization, provider=TestKeys(), mcp=env.mcp
    )
    env.skills = build_skill_service(env.engine, env.iam.authorization, store, env.tools.management)
    env.prompts = build_prompt_service(env.engine, env.iam.authorization, store)
    env.usage = build_usage_services(env.engine, env.services.channels, store)
    env.runs = build_run_service(
        env.engine,
        env.iam,
        core.versions,
        env.usage.budgets,
        env.usage.ledger,
        cleanup=core.cleanup,
    )
    env.conversations = build_conversation_service(
        env.engine, env.iam.authorization, env.runs, store
    )
    env.memory = build_memory_service(env.engine, env.iam.authorization)
    env.agents = build_agent_service(
        env.engine, env.iam.authorization, env.tools.management, env.skills, env.usage.budgets
    )
    env.adapter = Adapter()
    env.models = replace(
        build_model_services(
            env.engine, env.iam, key_provider=TestKeys(), resolver=env.model_resolver
        ),
        adapter=env.adapter,
    )
    env.provider = await env.models.configuration.save_provider(
        env.admin,
        ProviderInput(code="controlled", name="受控模型替身", protocols=["chat_completions"]),
    )
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
    for name in [
        "tools",
        "mcp",
        "skills",
        "prompts",
        "runs",
        "conversations",
        "agents",
        "models",
        "runtime",
        "usage",
        "memory",
    ]:
        setattr(app.state, name, getattr(env, name))
    app.state.core, app.state.integrations, app.state.delegation = (
        core,
        env.bundle,
        env.bundle.delegation,
    )
    app.state.sync_wait_seconds = 0.03
    app.state.sse_poll_seconds = 0.01
    app.state.sse_heartbeat_seconds = 0.05
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    env.api_url = f"http://127.0.0.1:{sock.getsockname()[1]}"
    server = uvicorn.Server(uvicorn.Config(app, lifespan="off", log_level="error"))
    server_task = asyncio.create_task(server.serve(sockets=[sock]))
    web_port = free_port()
    env.web_url = f"http://127.0.0.1:{web_port}"
    web_log = (folder / "web-server.log").open("w")
    web = await asyncio.create_subprocess_exec(
        node_path(),
        str(WEB / "node_modules/vite/bin/vite.js"),
        "preview",
        "--host",
        "127.0.0.1",
        "--port",
        str(web_port),
        "--strictPort",
        cwd=WEB,
        stdout=web_log,
        stderr=web_log,
    )
    env.browser_sessions = {}
    env.worker_enabled = False
    handled = set()

    async def worker():
        while True:
            if env.worker_enabled:
                async with env.engine.connect() as conn:
                    pending = (
                        (
                            await conn.execute(
                                text("SELECT id, channel_id FROM runs WHERE state='QUEUED'")
                            )
                        )
                        .mappings()
                        .all()
                    )
                for row in pending:
                    if row["id"] not in handled:
                        handled.add(row["id"])
                        execution = asyncio.create_task(
                            execute_message(
                                env.runs,
                                TaskEnvelope(channel_id=row["channel_id"], run_id=row["id"]),
                                "independence-worker",
                                env.runtime,
                            )
                        )
                        with (folder / "worker.jsonl").open("a") as stream:
                            stream.write(
                                json.dumps({"run_id": row["id"], "event": "started"}) + "\n"
                            )
                        try:
                            await execution
                        except asyncio.CancelledError:
                            if asyncio.current_task().cancelling():
                                raise
                        finally:
                            with (folder / "worker.jsonl").open("a") as stream:
                                stream.write(
                                    json.dumps(
                                        {
                                            "run_id": row["id"],
                                            "event": "returned",
                                            "cancelled": execution.cancelled(),
                                        }
                                    )
                                    + "\n"
                                )
            await asyncio.sleep(0.02)

    worker_task = asyncio.create_task(worker())
    try:
        async with asyncio.timeout(15):
            async with httpx.AsyncClient() as http:
                for _ in range(750):
                    if server.started:
                        break
                    await asyncio.sleep(0.02)
                while True:
                    try:
                        if (await http.get(env.web_url)).status_code == 200:
                            break
                    except httpx.HTTPError:
                        pass
                    await asyncio.sleep(0.05)
        yield env
        if worker_task.done():
            worker_task.result()
    finally:
        worker_task.cancel()
        try:
            await worker_task
        except asyncio.CancelledError:
            pass
        server.should_exit = True
        await asyncio.wait_for(server_task, 10)
        sock.close()
        web.terminate()
        await asyncio.wait_for(web.wait(), 10)
        web_log.close()


def allow_source(env, channel_id, source):
    # 精确登记测试渠道和随机端口，模拟现有服务端出站配置，不添加通配权限。
    env.outbound.destinations += (
        Destination(
            channel_id,
            "test",
            "mcp",
            "127.0.0.1",
            port=source.port,
            scheme="http",
            allowed_networks=("127.0.0.1/32",),
        ),
    )
