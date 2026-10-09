"""独立 MySQL 数据库、Redis 身份与委托签名凭据。"""

import base64
from datetime import timedelta

import pytest
from pydantic import SecretBytes

from creativity_service.core.primitives import utcnow
from creativity_service.modules.integrations.assembly import build_integration_services
from creativity_service.modules.integrations.schemas import DelegationKeyCreate
from tests.integration.channels.conftest import channel_env, credential, provision

__all__ = ["channel_env"]


class TestKeys:
    async def current(self):
        return "test", SecretBytes(bytes(range(32)))

    async def resolve(self, version):
        assert version == "test"
        return SecretBytes(bytes(range(32)))


@pytest.fixture
async def integration_env(channel_env):
    env = channel_env
    env.channel = await provision(env)
    env.identity = await credential(env, env.channel)
    env.context = env.channel.manager.context
    env.bundle = build_integration_services(env.engine, env.iam.authorization, provider=TestKeys())
    app = env.client._transport.app
    app.state.integrations, app.state.delegation = env.bundle, env.bundle.delegation
    env.key = await env.bundle.keys.create(
        env.context,
        DelegationKeyCreate(
            client_id=env.identity.client.client_id,
            issuer="business.example",
            audience="creativity-api",
            expires_at=utcnow() + timedelta(days=1),
        ),
    )
    env.secret = base64.b64decode(env.key.signing_secret)
    yield env
