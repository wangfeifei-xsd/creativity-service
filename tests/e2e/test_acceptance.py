"""26 复用 23 的三渠道接入，在当前构建上补齐模型、预算和提示词页面。"""

import asyncio
import json
import os
from pathlib import Path
from types import SimpleNamespace

import pytest

from creativity_service.modules.models.schemas import CaseResult
from creativity_service.modules.models.schemas import TestCompletion as Completion
from creativity_service.modules.models.schemas import TestInput as Cases
from tests.integration.channels.conftest import channel_env as channel_env
from tests.integration.models.test_models import Executor
from tests.integration.runtime import test_business_independence as independence
from tests.support.independence_evidence import ROOT, WEB
from tests.support.independence_runtime import node_path

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        not os.environ.get("CREATIVITY_ACCEPTANCE_DIR"),
        reason="当前构建组合验收须由 scripts/verify_acceptance.py 显式启动",
    ),
]


async def management_page(env, tenant, stage, **values):
    process = await asyncio.create_subprocess_exec(
        node_path(),
        str(WEB / "tests/e2e/acceptance-management.mjs"),
        cwd=WEB,
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    payload = {
        "stage": stage,
        "api": env.api_url,
        "web": env.web_url,
        "sessionToken": tenant.token,
        "provider": env.provider.name,
        **values,
    }
    try:
        async with asyncio.timeout(180):
            output, error = await process.communicate(json.dumps(payload).encode())
        diagnostic = error.decode().replace(tenant.token, "[已脱敏]")
        assert process.returncode == 0, diagnostic
        result = json.loads(output)
        folder = Path(os.environ["CREATIVITY_ACCEPTANCE_DIR"]) / "management"
        folder.mkdir(parents=True, exist_ok=True)
        (folder / f"{env.context.scope.channel_id}-{stage}.json").write_text(
            json.dumps(result, ensure_ascii=False, indent=2) + "\n"
        )
        return result
    finally:
        if process.returncode is None:
            process.kill()
            await process.wait()


async def configure_via_pages(env, tenant):
    env.context, env.tenant = tenant.manager.context, tenant
    env.adapter.before = None
    env.adapter.responses = []
    configured = await management_page(env, tenant, "model")
    env.model = SimpleNamespace(**configured["model"])
    env.models.configuration.executor = Executor()
    cases = ["text", "schema", "tools", "stream_cancel", "usage"]
    validation = await env.models.testing.create(tenant.manager, env.model.id, Cases(cases=cases))
    # 能力回调与模型响应均为隔离测试替身，不能导出为真实供应商或正式发布证明。
    await env.models.testing.complete(
        env.context,
        validation.id,
        Completion(
            run_id=validation.run_id,
            config_digest=validation.config_digest,
            results=[
                CaseResult(case=name, passed=True, attempt_ids=["fixture_26_" + name])
                for name in cases
            ],
            latency_ms=1,
            evidence="live",
        ),
    )
    env.worker_enabled = True
    await management_page(
        env,
        tenant,
        "dependencies",
        prompt=json.loads((ROOT / "examples/agents/prompts/text-brief.json").read_text()),
    )
    env.worker_enabled = False


async def test_current_build_three_channels_complete_management_flow(
    channel_env, tmp_path, monkeypatch
):
    folder = Path(os.environ["CREATIVITY_ACCEPTANCE_DIR"]) / "onboarding"
    monkeypatch.setenv("CREATIVITY_INDEPENDENCE_EVIDENCE_DIR", str(folder))
    monkeypatch.setattr(independence, "model_config", configure_via_pages)
    await independence.test_fixed_build_two_businesses_and_third_channel_via_management_pages(
        channel_env, tmp_path
    )
