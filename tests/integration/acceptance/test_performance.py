"""固定本机规模的管理查询与受理测量，不包含模型等待。"""

import asyncio
import math
import os
import platform
from time import perf_counter
from types import SimpleNamespace

import pytest

from creativity_service.core.primitives import RunInput
from creativity_service.core.security.outbound import Destination
from creativity_service.modules.budgets.schemas import BudgetCreate, PlatformLimitCreate
from creativity_service.modules.models.schemas import ProviderInput
from creativity_service.modules.usage.assembly import build_usage_services
from tests.integration.agents.test_agents import publish
from tests.integration.channels.conftest import provision
from tests.integration.channels.test_prompts_http import MemoryStore
from tests.support.acceptance_performance import http_clients
from tests.support.independence_evidence import ROOT, tree
from tests.support.independence_runtime import model_config

from .test_capacity import record

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        os.environ.get("CREATIVITY_ACCEPTANCE_PERFORMANCE") != "1",
        reason="性能测量须单独执行，避免其他回归进程影响时延",
    ),
]


def statistics(values, target):
    ordered = sorted(values)
    p95 = ordered[math.ceil(0.95 * len(ordered)) - 1]
    return {
        "samples": len(values),
        "p50_ms": round(ordered[math.ceil(0.5 * len(ordered)) - 1], 3),
        "p95_ms": round(p95, 3),
        "max_ms": round(ordered[-1], 3),
        "target_ms": target,
        "passed": p95 <= target,
        "durations_ms": [round(value, 3) for value in values],
    }


async def parallel(*operations):
    # 一项失败时取消并等待其余请求，避免测试结束后仍有事务阻塞隔离 schema 清理。
    async with asyncio.TaskGroup() as group:
        tasks = [group.create_task(operation) for operation in operations]
    return [task.result() for task in tasks]


@pytest.mark.parametrize(
    "agent_env",
    [{"environment": "test", "independent_actions": ["release:publish", "data:read_sensitive"]}],
    indirect=True,
)
async def test_twenty_clients_management_and_admission(runtime_env):
    env = runtime_env
    started_source = tree(ROOT, ["src", "pyproject.toml", "uv.lock", "deploy"])["sha256"]
    initial_load = os.getloadavg()
    env.usage = build_usage_services(env.engine, env.services.channels, MemoryStore())
    env.provider = await env.models.configuration.save_provider(
        env.admin,
        ProviderInput(code="capacity", name="性能测量替身", protocols=["chat_completions"]),
    )
    contexts = []
    tokens = []
    tenants = []
    await env.usage.management.platform_limits(
        env.admin,
        PlatformLimitCreate(
            limit_code="performance", name="平台并发二十", unit="concurrency", limit_value=20
        ),
    )
    for index in range(4):
        tenant = (
            env.tenant
            if index == 0
            else await provision(
                env,
                f"perf_{index}",
                None,
                independent_actions=["release:publish", "data:read_sensitive"],
            )
        )
        if index:
            env.models.configuration.outbound.destinations += (
                Destination(tenant.channel.channel_id, "test", "model", "models.example"),
            )
            await model_config(
                env, SimpleNamespace(manager=tenant.manager, token=tenant.token.access_token)
            )
        context = tenant.manager.context
        tenants.append(tenant)
        # 预算属于发布快照，须在固定候选之前配置，不能在发布后修改测试条件。
        await env.usage.management.save_budget(
            tenant.manager,
            BudgetCreate(
                name="渠道并发五",
                scope_type="channel",
                scope_id=context.scope.channel_id,
                unit="concurrency",
                limit_value="5",
            ),
        )
        for number in range(10):
            detail = await env.agents.create(
                context,
                env.body.model_copy(
                    update={"agent_code": f"perf_{number}", "definition": env.definition}
                ),
            )
            if number == 0:
                await publish(SimpleNamespace(context=context, agents=env.agents), detail)
        contexts.append(context)
        tokens.append(tenant.token.access_token)
    query_times, admission_times, http_times = [], [], []
    async with http_clients(env, tenants) as clients:
        for wave in range(6):

            async def query(index, wave=wave):
                started = perf_counter()
                response = await clients.management.get(
                    "/admin/v1/agents", headers={"Authorization": "Bearer " + tokens[index % 4]}
                )
                elapsed = (perf_counter() - started) * 1000
                assert response.status_code == 200, response.text
                assert len(response.json()["items"]) == 10
                if wave:
                    query_times.append(elapsed)

            await parallel(*(query(index) for index in range(20)))
            print(f"第 {wave + 1} 批管理查询完成", flush=True)

            async def submit(index, wave=wave):
                context = contexts[index % 4]
                started = perf_counter()
                receipt = await env.runs.admit_run(
                    context,
                    RunInput(agent_code="perf_0", input={"request": "请处理"}),
                    f"{wave}-{index}",
                )
                elapsed = (perf_counter() - started) * 1000
                if wave:
                    admission_times.append(elapsed)
                return context, receipt.run_id

            accepted = await parallel(*(submit(index) for index in range(20)))
            assert len({run_id for _, run_id in accepted}) == 20
            # 本批测量只受理后取消，排队二十不等于正在执行二十次外部模型调用。
            await parallel(*(env.runs.cancel(context, run_id) for context, run_id in accepted))
            print(f"第 {wave + 1} 批服务受理完成", flush=True)

            async def http_submit(index, wave=wave):
                started = perf_counter()
                receipt = await clients.backends[index % 4].submit(
                    "perf_0", {"request": "请处理"}, f"http-{wave}-{index}"
                )
                if wave:
                    http_times.append((perf_counter() - started) * 1000)
                return contexts[index % 4], receipt["run_id"]

            accepted = await parallel(*(http_submit(index) for index in range(20)))
            assert len({run_id for _, run_id in accepted}) == 20
            await parallel(*(env.runs.cancel(context, run_id) for context, run_id in accepted))
            print(f"第 {wave + 1} 批 HTTP 受理完成", flush=True)
    report = {
        "host": {
            "system": platform.system(),
            "machine": platform.machine(),
            "cpu_count": os.cpu_count(),
            "initial_load_average": initial_load,
            "final_load_average": os.getloadavg(),
        },
        "source": {
            "service_sha256": started_source,
            "unchanged_during_measurement": started_source
            == tree(ROOT, ["src", "pyproject.toml", "uv.lock", "deploy"])["sha256"],
        },
        "data": {
            "channels": 4,
            "agents_per_channel": 10,
            "runs_admitted": 240,
            "measured_requests_per_kind": 100,
            "warmup_per_kind": 20,
        },
        "concurrency": {
            "clients": 20,
            "channel_limit": 5,
            "platform_limit": 20,
            "model_calls_during_measurement": 0,
            "subject_review": "完整 HTTP 受理经真实 TCP MCP 复核；外部延时未人为注入",
        },
        "management_query": statistics(query_times, 1000),
        "admission_service": statistics(admission_times, 500),
        "admission_http_including_subject_review": statistics(http_times, 500),
        "boundary": (
            "管理查询经过 TCP、Redis 鉴权及真实 PostgreSQL；服务受理从已认证上下文调用正式服务，"
            "单独反映无外部等待的开销；业务 HTTP 受理包含签名委托和真实 MCP 主体复核，"
            "该总时延不能直接等同于扣除外部等待后的平台开销。所有模型执行均排除。"
        ),
    }
    record("performance.json", report)
    assert report["source"]["unchanged_during_measurement"]
    assert report["management_query"]["passed"], report["management_query"]
    assert report["admission_service"]["passed"], report["admission_service"]
