"""IAM-F03—F06、INT-A02/A08：真实委托校验、轮换及互斥重放。"""

import asyncio
from datetime import timedelta
from typing import Annotated

import pytest

from creativity_service.core.context import AuthContext
from creativity_service.core.primitives import ServiceError, utcnow
from creativity_service.integrations.business.delegation import DelegationClaims, bind_request, sign
from creativity_service.modules.channels.schemas import KeyRotate, TokenExchange
from creativity_service.modules.integrations.repositories import environment_scope, repository
from creativity_service.modules.integrations.schemas import DelegationKeyRotate

pytestmark = pytest.mark.integration


def claims(env, **changes):
    now = int(utcnow().timestamp())
    value = dict(
        subject_type="MEMBER",
        subject_id="user-1",
        data_scope={"type": "default", "id": "default"},
        actions=["run:create", "run:read"],
        resources={"agent": ["agent-1"], "run": ["run-1"]},
        issuer="business.example",
        audience="creativity-api",
        issued_at=now,
        expires_at=now + 300,
        nonce="random_nonce_12345678",
        request=bind_request("POST", "/api/v1/runs", b'{"input":"hello"}', "retry-1"),
    )
    value.update(changes)
    return DelegationClaims.model_validate(value)


async def verify(env, claim, context=None, actual=None):
    return await env.bundle.delegation.verify(
        context or env.identity.context,
        sign(claim, env.key.key.kid, env.secret),
        actual or claim.request,
    )


async def test_safe_concurrent_replay_and_changed_nonce_payload(integration_env):
    env = integration_env
    claim = claims(env)
    contexts = await asyncio.gather(*(verify(env, claim) for _ in range(8)))
    assert len({c.delegation_id for c in contexts}) == 1
    resolved = contexts[0]
    assert resolved.scope.data_scope_id == env.channel.domain.data_scope_id
    assert (await env.bundle.delegation.read_current(resolved)).scope == resolved.scope
    async with env.engine.connect() as connection:
        rows = await repository(environment_scope(resolved.scope), "delegation_nonces").find(
            connection
        )
    assert len(rows) == 1
    for modified in (
        claims(env, subject_id="user-2"),
        claims(
            env, request=bind_request("POST", "/api/v1/runs", b'{"input":"changed"}', "retry-1")
        ),
        claims(env, request=bind_request("GET", "/api/v1/runs/other", b"")),
    ):
        with pytest.raises(ServiceError) as failure:
            await verify(env, modified)
        assert failure.value.code == "DELEGATION_REPLAY"
    with pytest.raises(ServiceError, match="实际请求"):
        await verify(
            env, claim, actual=bind_request("POST", "/api/v1/runs", b"different", "retry-1")
        )


@pytest.mark.parametrize(
    "change,code",
    [
        ({"audience": "other-api"}, "DELEGATION_INVALID"),
        ({"issuer": "untrusted"}, "DELEGATION_INVALID"),
        ({"environment": "prod"}, "DELEGATION_SCOPE_INVALID"),
        ({"channel_id": "other-channel"}, "DELEGATION_SCOPE_INVALID"),
        ({"actions": ["run:create", "memory:read"]}, "DELEGATION_FORBIDDEN"),
        ({"data_scope": {"type": "club", "id": "other-club"}}, "DELEGATION_SCOPE_INVALID"),
        ({"issued_at": 1, "expires_at": 301}, "DELEGATION_EXPIRED"),
        ({"subject_type": "anonymous", "actions": ["memory:read"]}, "DELEGATION_FORBIDDEN"),
    ],
)
async def test_untrusted_claims_are_rejected(integration_env, change, code):
    with pytest.raises(ServiceError) as failure:
        await verify(integration_env, claims(integration_env, **change))
    assert failure.value.code == code


async def test_signature_checked_before_mapping_and_token_required(integration_env, monkeypatch):
    env = integration_env
    claim = claims(env, data_scope={"type": "club", "id": "secret-club"})

    async def forbidden(*args):
        pytest.fail("伪造签名不得查询源数据域")

    monkeypatch.setattr(
        "creativity_service.modules.integrations.delegation.source_mapping", forbidden
    )
    with pytest.raises(ServiceError) as failure:
        await env.bundle.delegation.verify(
            env.identity.context, sign(claim, env.key.key.kid, b"x" * 32), claim.request
        )
    assert failure.value.code == "DELEGATION_INVALID"
    response = await env.client.get(
        "/api/v1/artifacts/missing/content",
        headers={"Authorization": f"Bearer {env.identity.key.api_key}"},
    )
    assert response.status_code == 401


async def test_independent_key_rotation_and_stable_client_retry(integration_env):
    env = integration_env
    claim = claims(env)
    first = await verify(env, claim)
    rotated = await env.services.keys.rotate(
        env.channel.manager,
        env.channel.channel.channel_id,
        env.identity.key.key.key_id,
        KeyRotate(
            revision=env.identity.key.key.revision,
            expires_at=utcnow() + timedelta(days=1),
            overlap_seconds=60,
        ),
    )
    token = await env.services.keys.exchange(
        TokenExchange(api_key=rotated.api_key), "rotate-request"
    )
    context = await env.iam.authentication.authenticate(token.access_token, "service")
    repeated = await verify(env, claim, context=context)
    assert context.key_id != first.key_id and context.client_id == first.client_id
    assert repeated.delegation_id == first.delegation_id
    issued = await env.bundle.keys.rotate(
        env.context,
        env.key.key.kid,
        DelegationKeyRotate(
            revision=env.key.key.revision,
            expires_at=utcnow() + timedelta(days=2),
            overlap_seconds=60,
        ),
    )
    assert issued.key.rotated_from == env.key.key.kid
    assert (await verify(env, claim)).delegation_id == first.delegation_id
    current = next(k for k in await env.bundle.keys.list(env.context) if k.kid == env.key.key.kid)
    await env.bundle.keys.revoke(env.context, current.kid, current.revision)
    with pytest.raises(ServiceError):
        await verify(env, claim)


async def test_http_delegation_and_anonymous_boundary(integration_env):
    env = integration_env
    from fastapi import Depends

    from creativity_service.core.context import require_http_context

    app = env.client._transport.app

    @app.post("/api/v1/delegation-probe")
    async def probe(context: Annotated[AuthContext, Depends(require_http_context)]):
        return {"subject": context.scope.subject_id, "scope": context.scope.data_scope_id}

    body = b'{"message":"test"}'
    claim = claims(
        env,
        subject_type="anonymous",
        request=bind_request("POST", "/api/v1/delegation-probe", body),
    )
    headers = {
        "Authorization": f"Bearer {env.identity.token.access_token}",
        "X-Business-Delegation": sign(claim, env.key.key.kid, env.secret),
    }
    response = await env.client.post("/api/v1/delegation-probe", content=body, headers=headers)
    assert response.status_code == 200, response.text
    assert response.json()["scope"] == env.channel.domain.data_scope_id
    response = await env.client.post(
        "/api/v1/delegation-probe",
        content=body,
        headers={"Authorization": headers["Authorization"]},
    )
    assert response.status_code == 401
    verified = await verify(env, claim)
    result = await env.bundle.management.invoke(
        verified, env.connection.integration_id, "dictionary", {}, "run-1"
    )
    assert result.items[0]["name"] == "授权游戏名称"


async def test_worker_requires_current_subject_and_never_widens_permissions(
    integration_env, monkeypatch
):
    from creativity_service.core.auth.types import SubjectAuthority

    env = integration_env
    context = await verify(env, claims(env, resources={"run": ["*"]}))
    worker = context.model_copy(
        update={
            "principal_type": "worker",
            "session_id": None,
            "token_digest": None,
            "granted_actions": frozenset(),
        }
    )
    future = utcnow() + timedelta(seconds=400)
    monkeypatch.setattr("creativity_service.modules.integrations.delegation.utcnow", lambda: future)
    with pytest.raises(ServiceError) as failure:
        await env.bundle.delegation.read_current(context)
    assert failure.value.code == "DELEGATION_EXPIRED"
    with pytest.raises(ServiceError) as failure:
        await env.bundle.delegation.read_current(worker)
    assert failure.value.code == "DEPENDENCY_UNAVAILABLE"

    class Current:
        extra = False

        async def read_current(self, current, original):
            assert current.scope.subject_id == original.subject_id == "user-1"
            return SubjectAuthority(
                scope=current.scope,
                expires_at=future + timedelta(seconds=60),
                actions=frozenset({"run:read", "memory:read"} if self.extra else {"run:read"}),
                agent_actions=frozenset({"run:read"}),
                resources={"run": frozenset({"run-1"})},
            )

    source = Current()
    env.bundle.delegation.current_subjects = source
    authority = await env.bundle.delegation.read_current(worker)
    assert authority.actions == {"run:read"}
    source.extra = True
    with pytest.raises(ServiceError) as failure:
        await env.bundle.delegation.read_current(worker)
    assert failure.value.code == "DELEGATION_FORBIDDEN"
