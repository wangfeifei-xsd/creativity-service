"""渠道 Key 与密钥独立完成业务接入，不依赖委托配置或 MCP 身份工具。"""

import asyncio
from datetime import timedelta
from typing import Annotated

import pytest
from fastapi import Depends

from creativity_service.core.auth.types import IdentitySource
from creativity_service.core.context import AuthContext, require_http_context
from creativity_service.core.database import transaction
from creativity_service.core.primitives import ServiceError, utcnow
from creativity_service.modules.agents.access import locked_policy
from creativity_service.modules.channels.schemas import KeyCreate, KeyRotate, TokenExchange
from creativity_service.modules.iam.repositories import policy_key

from .conftest import provision

pytestmark = pytest.mark.integration


async def access(env, channel):
    created = await env.services.keys.create(
        channel.manager,
        channel.channel.channel_id,
        KeyCreate(name="业务接口", environment="test", expires_at=utcnow() + timedelta(days=1)),
    )
    token = await env.services.keys.exchange(
        TokenExchange(key_id=created.key.key_id, secret=created.api_key), "channel-access-test"
    )
    return created, token


def probe(env):
    @env.client._transport.app.get("/api/v1/access-probe")
    async def endpoint(context: Annotated[AuthContext, Depends(require_http_context)]):
        await env.iam.authorization.boundary(context, "run:create", "agent", "new")
        return context.scope.model_dump()


async def test_channel_pair_without_delegation_or_mcp_and_subject_scope(channel_env):
    env = channel_env
    channel = await provision(env)
    key, token = await access(env, channel)
    assert "content:delete" in key.key.scopes
    probe(env)
    headers = {"Authorization": f"Bearer {token.access_token}"}
    result = await env.client.get("/api/v1/access-probe", headers=headers)
    assert result.status_code == 200, result.text
    assert result.json() == {
        "channel_id": channel.channel.channel_id,
        "environment": "test",
        "subject_type": "service",
        "subject_id": key.key.client_id,
    }
    result = await env.client.get(
        "/api/v1/access-probe",
        headers={
            **headers,
            "X-Subject-Type": "user",
            "X-Subject-ID": "player_1",
            "X-Channel-ID": "foreign",
            "X-Environment": "prod",
        },
    )
    assert result.status_code == 200, result.text
    assert result.json()["channel_id"] == channel.channel.channel_id
    assert result.json()["environment"] == "test"
    assert result.json()["subject_id"] == "player_1"
    for extra in (
        {"X-Subject-ID": "player_1"},
        {"X-Subject-Type": "user"},
        {"X-Subject-Type": "user", "X-Subject-ID": "invalid/id"},
    ):
        response = await env.client.get("/api/v1/access-probe", headers={**headers, **extra})
        assert response.status_code == 422


async def test_wrong_pair_missing_pair_and_other_channel_rejected(channel_env):
    env = channel_env
    first = await provision(env)
    second = await provision(env, "playmate", "club")
    key, token = await access(env, first)
    other, _ = await access(env, second)
    for body, status in [
        ({"key_id": key.key.key_id}, 422),
        ({"secret": key.api_key}, 422),
        ({"key_id": other.key.key_id, "secret": key.api_key}, 401),
        ({"key_id": key.key.key_id, "secret": "wrong"}, 401),
    ]:
        response = await env.client.post("/api/v1/auth/token", json=body)
        assert response.status_code == status, response.text
        assert key.api_key not in response.text
    context = await env.iam.authentication.authenticate(token.access_token, "service")
    with pytest.raises(ServiceError) as denied:
        await env.iam.authorization.boundary(
            context, "run:create", "channel", second.channel.channel_id
        )
    assert denied.value.status == 404


async def test_internal_dependencies_and_worker_use_current_channel_key(channel_env):
    env = channel_env
    channel = await provision(env)
    key, token = await access(env, channel)
    context = await env.iam.authentication.authenticate(token.access_token, "service")
    worker = (await env.iam.authentication.identity_source(context)).worker_context("worker-test")
    assert isinstance(await env.iam.authentication.identity_source(context), IdentitySource)
    async with transaction(
        env.engine, context.scope, [policy_key("system"), policy_key(context.scope.channel_id)]
    ) as uow:
        policy = await locked_policy(uow, worker)
        for kind in ("agent", "model", "prompt", "skill", "tool"):
            policy.require(worker, "run:create", kind, "platform-managed-resource")
    await env.services.keys.revoke(
        channel.manager, channel.channel.channel_id, key.key.key_id, key.key.revision
    )
    for current in (context, worker):
        with pytest.raises(ServiceError):
            await env.iam.authorization.boundary(current, "run:create", "agent", "new")


async def test_concurrent_channel_keys_share_identity_and_rotation(channel_env):
    env = channel_env
    channel = await provision(env)
    issued = await asyncio.gather(*(access(env, channel) for _ in range(2)))
    assert len({key.key.client_id for key, _ in issued}) == 1
    key, token = issued[0]
    new = await env.services.keys.rotate(
        channel.manager,
        channel.channel.channel_id,
        key.key.key_id,
        KeyRotate(
            revision=key.key.revision, expires_at=utcnow() + timedelta(days=2), overlap_seconds=0
        ),
    )
    assert new.key.client_id == key.key.client_id
    with pytest.raises(ServiceError):
        await env.iam.authentication.authenticate(token.access_token, "service")
    await env.services.keys.exchange(
        TokenExchange(key_id=new.key.key_id, secret=new.api_key), "rotated"
    )


async def test_old_configuration_endpoints_removed(channel_env):
    for path in ("/admin/v1/delegation-keys", "/admin/v1/subject-review-bindings"):
        response = await channel_env.client.get(path)
        assert response.status_code == 404
