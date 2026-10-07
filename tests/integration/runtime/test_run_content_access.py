"""渠道管理权限读取真实调试记录；不额外授予敏感原文，撤权后立即收窄。"""

import pytest

from creativity_service.core.database import Repository, transaction
from creativity_service.core.locking import record_key
from creativity_service.modules.iam.repositories import policy_key
from creativity_service.modules.iam.tables import metadata
from creativity_service.workers.executor import execute_message
from tests.integration.runtime.test_execution import admitted

pytestmark = pytest.mark.integration


async def test_channel_manager_reads_debug_content_and_revocation_hides_it(runtime_env):
    env = runtime_env
    receipt, message = await admitted(env)
    await execute_message(env.runs, message, "manual_debug_worker", env.runtime)
    path = f"/admin/v1/runs/{receipt.run_id}"
    permissions = await env.iam.authorization.allowed_actions(env.context, "run", receipt.run_id)
    assert "data:read_sensitive" not in permissions
    response = await env.client.get(path + "/detail")
    assert response.status_code == 200
    detail = response.json()
    assert detail["content_allowed"]
    assert detail["input"] == {"request": "请处理"}
    assert detail["result"]["result"]["data"] == {"answer": "验证完成"}
    assert detail["versions"] and detail["actual_inputs"]
    assert (await env.client.get(path)).status_code == 200
    events = await env.client.get(path + "/events")
    assert events.status_code == 200 and "event: result\n" in events.text

    # 模拟已提交的渠道管理权撤回，保留运行元数据权；当前 Token 的下一请求须生效。
    grants = await env.iam.accounts.repository.grants(env.context.scope.channel_id)
    owned = [grant for grant in grants if grant.grantee_id == env.context.actor_id]
    assert owned
    repo = Repository(metadata.tables["resource_grants"], env.context.scope)
    async with transaction(
        env.engine,
        env.context.scope,
        [policy_key(env.context.scope.channel_id)]
        + [record_key(env.context.scope.channel_id, "resource_grants", g.id) for g in owned],
    ) as uow:
        for grant in owned:
            await repo.change(
                uow,
                grant.id,
                grant.revision,
                {"allowed_actions": [a for a in grant.allowed_actions if a != "channel:manage"]},
            )
    response = await env.client.get(path + "/detail")
    assert response.status_code == 200
    hidden = response.json()
    assert not hidden["content_allowed"]
    assert hidden["input"] is None and hidden["result"] is None
    assert hidden["versions"] == [] and hidden["actual_inputs"] == []
    assert (await env.client.get(path)).status_code == 403
    assert (await env.client.get(path + "/events")).status_code == 403
