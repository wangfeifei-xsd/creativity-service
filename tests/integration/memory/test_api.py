"""真实管理 Token、授权及业务接口边界。"""

import httpx
import pytest

from creativity_service.app import create_schema_app
from creativity_service.core.deletion import CleanupRegistry, ContentRef, RecoveryService
from creativity_service.core.primitives import new_id
from creativity_service.modules.iam.schemas import ChannelContextInput
from creativity_service.modules.memory.assembly import build_memory_service
from creativity_service.modules.memory.schemas import MemoryCreate
from tests.integration.channels.conftest import channel_body, channel_env, login
from tests.integration.core.conftest import TestAuthorization

pytestmark = pytest.mark.integration
__all__ = ["channel_env"]


async def test_http_real_iam_current_domain_revision_subject_and_token_revocation(channel_env):
    env = channel_env
    channel = await env.services.channels.create(
        env.admin,
        channel_body(env).model_copy(update={"independent_actions": ["data:read_sensitive"]}),
    )
    domains = await env.services.channels.data_scopes(env.admin, channel.channel_id)
    _, session = await login(env)
    token = await env.iam.sessions.enter(
        session,
        ChannelContextInput(
            channel_id=channel.channel_id,
            environment="test",
            data_scope_id=domains[0].data_scope_id,
        ),
    )
    context = (
        await env.iam.authentication.admin_session(token.access_token, new_id("request"))
    ).context
    subject = context.model_copy(
        update={
            "scope": context.scope.model_copy(update={"subject_type": "user", "subject_id": "one"})
        }
    )
    await RecoveryService(env.engine, TestAuthorization()).initialize_fresh(subject)
    cleanup = CleanupRegistry()
    memory = build_memory_service(env.engine, env.iam.authorization, cleanup)
    saved = await memory.create(
        subject, MemoryCreate(key="usual_budget", value={"min": 100, "max": 200, "currency": "CNY"})
    )
    app = create_schema_app()
    app.state.memory, app.state.authentication = memory, env.iam.authentication
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://test",
        headers={"Authorization": f"Bearer {token.access_token}"},
    ) as client:
        base = f"/admin/v1/memories/{saved.memory_id}"
        assert (await client.get(base)).status_code == 200
        assert (await client.get("/admin/v1/memories")).json()["items"][0][
            "memory_id"
        ] == saved.memory_id
        assert (await client.get("/admin/v1/memory-subjects")).status_code == 200
        assert (
            await client.post(
                "/admin/v1/memories",
                json={"key": "play_style", "value": "休闲", "channel_id": "other"},
            )
        ).status_code == 422
        response = await client.patch(
            base,
            json={"revision": saved.revision, "value": {"min": 200, "max": 300, "currency": "CNY"}},
        )
        assert response.status_code == 200, response.text
        assert (
            await client.patch(base, json={"revision": saved.revision, "value": 0})
        ).status_code == 409
        path = f"/admin/v1/memory-preferences?anchor_id={saved.memory_id}"
        assert (await client.put(path, json={"enabled": False, "revision": 0})).status_code == 200
        assert not (await client.get(path)).json()["enabled"]
        assert (await client.post("/admin/v1/memories/clear")).status_code == 422
        assert (await client.get(base.replace("/admin/", "/api/"))).status_code == 401
        deleted = await client.delete(base)
        assert deleted.status_code == 202, deleted.text
        assert (await client.get(base)).json()["memory"]["value"] is None
        await cleanup.clean(subject, ContentRef("memory", saved.memory_id))
        progress = f"/admin/v1/memory-deletions/{deleted.json()['deletion_id']}"
        assert (await client.get(progress)).json()["status"] == "WAITING_PROPAGATION"
        await env.redis.expire(env.iam.authentication.tokens.token_key(context.token_digest), 0)
        assert (await client.get(base)).status_code == 401
        assert (await client.get(progress)).status_code == 401


async def test_business_delegation_controls_own_memory_without_subject_filters(channel_env):
    import base64
    from datetime import timedelta
    from types import SimpleNamespace

    from creativity_service.core.primitives import canonical_json, utcnow
    from creativity_service.integrations.business.delegation import bind_request, sign
    from creativity_service.modules.channels.schemas import ClientCreate, KeyCreate, TokenExchange
    from creativity_service.modules.integrations.assembly import build_integration_services
    from creativity_service.modules.integrations.schemas import DelegationKeyCreate
    from tests.integration.integrations.conftest import TestKeys
    from tests.integration.integrations.test_delegation import claims

    env = channel_env
    created_channel = await env.services.channels.create(
        env.admin,
        channel_body(env).model_copy(update={"independent_actions": ["data:read_sensitive"]}),
    )
    domains = await env.services.channels.data_scopes(env.admin, created_channel.channel_id)
    _, session = await login(env)
    entered = await env.iam.sessions.enter(
        session,
        ChannelContextInput(
            channel_id=created_channel.channel_id,
            environment="test",
            data_scope_id=domains[0].data_scope_id,
        ),
    )
    manager = await env.iam.authentication.admin_session(
        entered.access_token, new_id("request"), governance=True
    )
    env.context = manager.context
    channel = SimpleNamespace(channel=created_channel, domain=domains[0], manager=manager)
    env.bundle = build_integration_services(env.engine, env.iam.authorization, provider=TestKeys())
    env.client._transport.app.state.delegation = env.bundle.delegation
    actions = [
        "memory:read",
        "memory:write",
        "memory:delete",
        "memory:preferences",
        "data:read_sensitive",
    ]
    client = await env.services.channels.create_client(
        channel.manager,
        channel.channel.channel_id,
        ClientCreate(
            name="记忆业务后端",
            environment="test",
            scopes=actions,
            data_scopes=[channel.domain.data_scope_id],
        ),
    )
    key = await env.services.keys.create(
        channel.manager,
        channel.channel.channel_id,
        KeyCreate(
            name="记忆接入凭据",
            client_id=client.client_id,
            environment="test",
            scopes=actions,
            expires_at=utcnow() + timedelta(minutes=20),
        ),
    )
    token = await env.services.keys.exchange(TokenExchange(api_key=key.api_key), new_id("request"))
    delegation_key = await env.bundle.keys.create(
        env.context,
        DelegationKeyCreate(
            client_id=client.client_id,
            issuer="business.example",
            audience="creativity-api",
            expires_at=utcnow() + timedelta(days=1),
        ),
    )
    env.client._transport.app.state.memory = build_memory_service(env.engine, env.iam.authorization)
    for subject_id in ("user-one", "user-two"):
        scoped = env.context.model_copy(
            update={
                "scope": env.context.scope.model_copy(
                    update={"subject_type": "MEMBER", "subject_id": subject_id}
                )
            }
        )
        await RecoveryService(env.engine, TestAuthorization()).initialize_fresh(scoped)

    async def request(method, path, body=None, subject="user-one"):
        raw = canonical_json(body) if body is not None else b""
        claim = claims(
            env,
            subject_id=subject,
            actions=actions,
            resources={"memory": ["*"]},
            nonce=new_id("nonce"),
            request=bind_request(method, path, raw),
        )
        return await env.client.request(
            method,
            path,
            content=raw,
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {token.access_token}",
                "X-Business-Delegation": sign(
                    claim, delegation_key.key.kid, base64.b64decode(delegation_key.signing_secret)
                ),
            },
        )

    created = await request("POST", "/api/v1/memories", {"key": "play_style", "value": "休闲"})
    assert created.status_code == 201, created.text
    path = f"/api/v1/memories/{created.json()['memory_id']}"
    assert (await request("GET", path)).status_code == 200
    assert (await request("GET", path, subject="user-two")).status_code == 404
    assert not (await request("GET", "/api/v1/memories", subject="user-two")).json()["items"]
    override = await request(
        "POST", "/api/v1/memories", {"key": "play_style", "value": "竞技", "subject_id": "user-two"}
    )
    assert override.status_code == 422
    assert (
        await request("PUT", "/api/v1/memory-preferences", {"enabled": False, "revision": 0})
    ).status_code == 200
    assert (await request("GET", path)).json()["memory"]["value"] == "休闲"
    assert (await request("POST", "/api/v1/memories/clear")).status_code == 202
    assert (await request("GET", path)).json()["memory"]["value"] is None
