"""任意渠道均按渠道和环境接入，不依赖源系统组织结构。"""

import pytest

from creativity_service.core.primitives import ServiceError

from .conftest import credential, provision

pytestmark = pytest.mark.integration


async def test_arbitrary_channel_issues_credentials_without_business_category(channel_env):
    env = channel_env
    a, b = await provision(env, "research"), await provision(env, "second-research")
    identity = await credential(env, a)
    assert identity.context.scope.channel_id == a.channel.channel_id
    with pytest.raises(ServiceError) as denied:
        await env.services.channels.detail(a.manager, b.channel.channel_id)
    assert denied.value.status == 404
