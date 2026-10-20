"""真实 MySQL 验证模型执行可用绑定凭据，且读取期间撤权阻断供应商请求。"""

import httpx
import pytest

from creativity_service.core.primitives import ServiceError
from creativity_service.integrations.models.contracts import ModelRequest
from creativity_service.modules.iam.schemas import MembershipInput
from creativity_service.modules.models.schemas import ModelGrantInput
from tests.integration.iam.conftest import create_user, enter
from tests.integration.models.test_models import complete, setup
from tests.models.test_protocols import fixture_attempt, fixture_reservation

pytestmark = pytest.mark.integration


@pytest.mark.parametrize("revoke_during_decryption", [False, True])
async def test_model_grant_uses_only_bound_credential_and_rechecks_revocation(
    channel_env, revoke_during_decryption
):
    tenant, services, _, _, _, model = await setup(channel_env)
    _, executor = await complete(services, tenant, model)
    account, (_, user) = await create_user(channel_env.iam, channel_env.admin)
    await channel_env.iam.access.put_member(
        tenant.manager,
        tenant.channel.channel_id,
        account.user_id,
        MembershipInput(roles=["builder"], environments=["test"]),
    )
    grant = await services.configuration.grant(
        tenant.manager,
        tenant.channel.channel_id,
        model.id,
        ModelGrantInput(grantee_type="account", grantee_id=account.user_id),
    )
    _, user = await enter(channel_env.iam, user, tenant.channel.channel_id)
    frozen = executor.submissions[0].configuration
    with pytest.raises(ServiceError) as denied:
        await channel_env.iam.authorization.require(
            user.context, "credential:use", frozen.provider_credential_id
        )
    assert denied.value.status == 403

    requests = []

    async def handle(request):
        requests.append(request)
        return httpx.Response(
            200,
            json={
                "id": "fixture-response",
                "choices": [
                    {"message": {"role": "assistant", "content": "已完成"}, "finish_reason": "stop"}
                ],
                "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
            },
        )

    client = services.adapter.client
    client.transport_factory = lambda: httpx.MockTransport(handle)
    if revoke_during_decryption:
        original_resolve = client.credentials.keys.resolve

        async def resolve(version):
            await channel_env.iam.access.revoke_grant(
                tenant.manager, tenant.channel.channel_id, grant.grant_id, grant.revision
            )
            return await original_resolve(version)

        client.credentials.keys.resolve = resolve

    events = [
        event
        async for event in services.adapter.events(
            user.context,
            frozen,
            ModelRequest(messages=[{"role": "user", "content": "你好"}]),
            fixture_attempt(frozen).model_copy(
                update={"target_version_id": frozen.model_version_id}
            ),
            fixture_reservation(frozen),
        )
    ]
    if revoke_during_decryption:
        assert events[-1].kind == "failed" and events[-1].error_code == "FORBIDDEN"
        assert requests == []
    else:
        assert events[-1].kind == "completed", events
        assert len(requests) == 1
        assert [event.text for event in events if event.kind == "text"] == ["已完成"]
    assert "never-return-this-secret" not in str(events)
