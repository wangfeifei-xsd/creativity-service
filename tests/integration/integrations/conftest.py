"""独立 PostgreSQL schema 与 Redis 身份；业务响应使用明确测试适配器。"""

import base64
from datetime import timedelta

import pytest
from pydantic import SecretBytes

from creativity_service.core.primitives import utcnow
from creativity_service.core.security.outbound import Destination, OutboundPolicy
from creativity_service.integrations.business.base import (
    BusinessAdapter,
    BusinessResult,
    Capability,
)
from creativity_service.integrations.business.registry import BusinessRegistry, Registration
from creativity_service.modules.integrations.assembly import build_integration_services
from creativity_service.modules.integrations.schemas import DelegationKeyCreate, IntegrationCreate
from tests.integration.channels.conftest import channel_env, credential, provision

__all__ = ["channel_env"]


class TestKeys:
    async def current(self):
        return "test", SecretBytes(bytes(range(32)))

    async def resolve(self, version):
        assert version == "test"
        return SecretBytes(bytes(range(32)))


class FixtureAdapter(BusinessAdapter):
    async def dictionary(self, call):
        return BusinessResult(
            operation="dictionary",
            source_request_id="fixture-source",
            source_version="fixture-v1",
            observed_at=utcnow(),
            items=[{"code": "game", "name": "授权游戏名称"}],
            has_more=False,
            coverage="测试字典全量",
        )


@pytest.fixture
async def integration_env(channel_env):
    env = channel_env
    env.channel = await provision(env)
    env.identity = await credential(env, env.channel)
    env.context = env.channel.manager.context

    async def resolve(host, port):
        return ["93.184.216.34"]

    outbound = OutboundPolicy(
        (Destination(env.context.scope.channel_id, "test", "http_tool", "business.example"),),
        resolve,
    )
    registry = BusinessRegistry()
    registry.register(
        Registration(
            "fixture",
            "契约夹具",
            "1",
            FixtureAdapter(),
            (
                Capability(
                    operation="dictionary",
                    name="业务字典",
                    public=True,
                    required_actions=["run:create"],
                ),
            ),
        )
    )
    env.bundle = build_integration_services(
        env.engine, env.iam.authorization, provider=TestKeys(), outbound=outbound, registry=registry
    )
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
    env.credential_ref = await env.bundle.credentials.store(
        env.context, "http_tool", SecretBytes(b"fixture-only")
    )
    env.connection_body = IntegrationCreate(
        name="业务连接",
        adapter_code="fixture",
        adapter_version="1",
        business_endpoint="https://business.example",
        credential_ref=env.credential_ref,
        allowed_operations=["dictionary"],
        operation_paths={"dictionary": "/dictionary"},
    )
    env.connection = await env.bundle.management.save(env.context, env.connection_body)
    yield env
