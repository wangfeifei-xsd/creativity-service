"""本地运行额度拦截、历史诊断和配置入口使用同一冻结依据与当前权限。"""

import pytest

from creativity_service.core.database import Repository, transaction
from creativity_service.core.locking import record_key
from creativity_service.core.primitives import ServiceError
from creativity_service.integrations.models.contracts import ModelRequest
from creativity_service.modules.iam.repositories import policy_key
from creativity_service.modules.iam.tables import metadata
from creativity_service.modules.models.schemas import FrozenModel
from creativity_service.modules.runs.repositories import save
from creativity_service.modules.runtime.storage import load_spec
from tests.integration.runtime.test_execution import admitted

pytestmark = pytest.mark.integration


async def test_token_limit_explains_local_preflight_and_historical_evidence(runtime_env):
    env = runtime_env
    receipt, message = await admitted(env)
    lease = await env.runs.claim_lease(message, "token-limit")
    spec = await load_spec(env.runs, await env.runs.load(message))
    route = next(v for v in spec.versions if v.resource_type == "model_route")
    candidates = [FrozenModel.model_validate(value) for value in route.content["models"]]
    request = ModelRequest(messages=[{"role": "user", "content": "测" * 6000}])
    with pytest.raises(ServiceError) as rejected:
        await env.runtime.models.invoke(
            env.context,
            lease,
            spec,
            spec.definition.start_step,
            request,
            candidates,
            route.content["attempt_order"],
        )
    assert rejected.value.code == "RUN_TOKEN_LIMIT"
    assert "本地智能体“业务助手”" in rejected.value.message
    assert "16,000 Token" in rejected.value.message
    assert f"{request.input_token_upper_bound():,}" in rejected.value.message
    assert "本次模型请求未发送" in rejected.value.message
    assert not env.adapter.calls
    await env.runs.finish_run(lease, "FAILED", failure=rejected.value)
    response = await env.client.get(f"/admin/v1/runs/{receipt.run_id}/detail")
    assert response.status_code == 200
    detail = response.json()
    assert detail["error"]["message"] == rejected.value.message
    assert detail["result"]["usage_summary"]["attempt_count"] == 0
    guidance = detail["error_guidance"]
    assert "运行策略 → 单次运行 Token 上限" in guidance["description"]
    assert guidance["configuration_path"] == (f"/agents/{spec.agent_id}?tab=limits")

    # 模拟旧版只保存笼统错误的运行；读取时用旧快照补充说明，不改写历史记录。
    async with transaction(
        env.engine, env.context.scope, env.runs.keys(env.context, receipt.run_id)
    ) as uow:
        await save(
            uow,
            "runs",
            receipt.run_id,
            {
                "error": {**detail["error"], "message": "运行 Token 额度已达上限"},
            },
        )
    historical = await env.runs.detail(env.context, receipt.run_id)
    assert historical["error"]["message"] == rejected.value.message
    assert (await env.runs.load(message))["error"]["message"] == "运行 Token 额度已达上限"

    # 撤回配置和原文权限后保留可读失败来源，不泄露输入证据或返回无权访问的入口。
    grants = await env.iam.accounts.repository.grants(env.context.scope.channel_id)
    owned = [grant for grant in grants if grant.grantee_id == env.context.actor_id]
    repo = Repository(metadata.tables["resource_grants"], env.context.scope)
    async with transaction(
        env.engine,
        env.context.scope,
        [policy_key(env.context.scope.channel_id)]
        + [
            record_key(env.context.scope.channel_id, "resource_grants", grant.id) for grant in owned
        ],
    ) as uow:
        for grant in owned:
            await repo.change(
                uow,
                grant.id,
                grant.revision,
                {
                    "allowed_actions": [
                        action
                        for action in grant.allowed_actions
                        if action not in {"channel:manage", "agent:manage"}
                    ],
                },
            )
    restricted = await env.client.get(f"/admin/v1/runs/{receipt.run_id}/detail")
    assert restricted.status_code == 200
    hidden = restricted.json()
    assert not hidden["content_allowed"]
    assert "16,000" in hidden["error"]["message"]
    assert "当前权限不允许查看额度计算所需的运行内容" in hidden["error"]["message"]
    assert hidden["error_guidance"]["configuration_path"] is None
    assert "联系渠道管理员" in hidden["error_guidance"]["description"]
