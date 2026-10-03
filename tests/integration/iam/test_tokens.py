"""IAM-A08/A10：固定期限、用途隔离、切换竞争与撤销索引。"""

import asyncio
from datetime import timedelta

import pytest

from creativity_service.core.auth.types import Revocation
from creativity_service.core.context import AuthContext, Scope
from creativity_service.core.primitives import ServiceError, new_id, utcnow

from .conftest import enter, login, provision

pytestmark = pytest.mark.integration


async def test_token_ttl_deletion_expiry_and_fixed_deadline(iam_env, admin):
    iam, client, _, _, redis = iam_env
    tokens = iam.authentication.tokens
    key = tokens.token_key(tokens.digest(admin[0].access_token))
    ttl = await redis.pttl(key)
    assert 0 < ttl <= 7_200_000
    assert admin[0].access_token.encode() not in await redis.get(key)
    headers = {"Authorization": f"Bearer {admin[0].access_token}"}
    assert (await client.get("/admin/v1/auth/session", headers=headers)).status_code == 200
    assert await redis.pttl(key) <= ttl
    await redis.persist(key)
    assert (await client.get("/admin/v1/auth/session", headers=headers)).status_code == 401
    await redis.pexpire(key, 20)
    await asyncio.sleep(0.03)
    assert (await client.get("/admin/v1/auth/session", headers=headers)).status_code == 401
    response, _ = await login(iam, "root-admin")
    await redis.delete(tokens.token_key(tokens.digest(response.access_token)))
    with pytest.raises(ServiceError) as exc:
        await iam.authentication.admin_session(response.access_token, new_id("request"))
    assert exc.value.status == 401


async def test_purposes_current_key_subject_caps_and_key_index(iam_env, admin, manager):
    iam, _, channels, _, redis = iam_env
    context = AuthContext(
        scope=Scope(channel_id="channel_a", environment="test"),
        principal_type="service",
        principal_id="client_a",
        client_id="client_a",
        key_id="key_a",
        request_id=new_id("request"),
    )
    service = await iam.sessions.issue_service(context)
    record = await iam.authentication.tokens.read(service.access_token, {"service"})
    assert service.expires_at <= channels.key_expires
    assert 0 < service.expires_in <= 300
    responses = {"login": admin[0], "management": manager[0], "service": service}
    for actual, response in responses.items():
        for requested in responses:
            if actual != requested:
                with pytest.raises(ServiceError) as exc:
                    await iam.authentication.tokens.read(response.access_token, {requested})
                assert exc.value.code == "TOKEN_PURPOSE_INVALID"
    with pytest.raises(ServiceError) as exc:
        await iam.authentication.authenticate(admin[0].access_token, "management")
    assert exc.value.status == 401
    bound = await iam.authentication.authenticate(service.access_token, "service")
    with pytest.raises(ServiceError):
        await iam.authorization.require(bound, "run:read", "run_a")
    subject = bound.model_copy(
        update={
            "scope": Scope(
                channel_id="channel_a",
                environment="test",
                data_scope_id="domain_a",
                subject_type="MEMBER",
                subject_id="same_number",
            )
        }
    )
    await iam.authorization.require(subject, "run:read", "run_a")
    channels.subject_actions = frozenset()
    with pytest.raises(ServiceError) as exc:
        await iam.authorization.require(subject, "run:read", "run_a")
    assert exc.value.status == 403
    channels.key_active = False
    with pytest.raises(ServiceError) as exc:
        await iam.authentication.authenticate(service.access_token, "service")
    assert exc.value.code == "CLIENT_REVOKED"
    channels.key_active = True
    channels.key_expires = utcnow() - timedelta(seconds=1)
    with pytest.raises(ServiceError):
        await iam.authentication.authenticate(service.access_token, "service")
    await iam.authentication.tokens.revoke(
        Revocation(
            id=new_id("revoke"),
            channel_id="channel_a",
            kind="key",
            target_id="key_a",
            cutoff_at=utcnow(),
        )
    )
    assert not await redis.exists(iam.authentication.tokens.token_key(record.token_digest))
    assert not await redis.exists(iam.authentication.tokens.index_key("channel_a", "key", "key_a"))


async def test_switch_is_atomic_single_winner_and_preserves_old_run_scope(iam_env, admin, manager):
    iam, _, _, _, redis = iam_env
    await provision(iam, admin[1], "channel_b", admin[1].account.id)
    old_context = manager[1].context
    results = await asyncio.gather(
        enter(iam, manager[1], "channel_b"),
        enter(iam, manager[1], "channel_b"),
        return_exceptions=True,
    )
    assert sum(isinstance(result, tuple) for result in results) == 1
    assert sum(isinstance(result, ServiceError) for result in results) == 1
    assert old_context.scope.channel_id == "channel_a"
    worker = old_context.model_copy(
        update={"principal_type": "worker", "session_id": None, "token_digest": None}
    )
    await iam.authorization.boundary(worker, "membership:read", "channel", "channel_a")
    with pytest.raises(ServiceError):
        await iam.authentication.authenticate(manager[0].access_token, "management")
    response, new_session = next(result for result in results if isinstance(result, tuple))
    assert new_session.context.scope.channel_id == "channel_b"
    with pytest.raises(ServiceError) as exc:
        await iam.access.list_members(new_session, "channel_a")
    assert exc.value.status == 404
    with pytest.raises(ServiceError):
        await iam.authentication.revalidate(
            new_session.context.model_copy(update={"scope": old_context.scope})
        )
    assert (
        await redis.zcard(
            iam.authentication.tokens.index_key("channel_a", "member", admin[1].account.id)
        )
        == 0
    )


async def test_pending_account_cleanup_does_not_delete_new_session(iam_env, admin):
    iam, _, _, _, redis = iam_env
    cutoff = utcnow()
    await asyncio.sleep(0.003)
    response, _ = await login(iam, "root-admin")
    await iam.authentication.tokens.revoke(
        Revocation(
            id=new_id("revoke"),
            channel_id="system",
            kind="account",
            target_id=admin[1].account.id,
            cutoff_at=cutoff,
        )
    )
    assert await redis.exists(
        iam.authentication.tokens.token_key(iam.authentication.tokens.digest(response.access_token))
    )
    with pytest.raises(ServiceError):
        await iam.authentication.admin_session(admin[0].access_token, new_id("request"))


async def test_existing_token_without_new_optional_fields_can_switch(iam_env, admin):
    import json

    iam, _, _, _, redis = iam_env
    tokens = iam.authentication.tokens
    key = tokens.token_key(tokens.digest(admin[0].access_token))
    original = json.loads(await redis.get(key))
    original.pop("upstream_expires_at", None)
    original.pop("identity_channel_id", None)
    await redis.set(key, json.dumps(original, separators=(",", ":")), keepttl=True)
    stored = await tokens.read(admin[0].access_token, {"login"})
    response, _ = await tokens.issue(
        purpose="login",
        principal_id=stored.principal_id,
        credential_version=stored.credential_version,
        replace=stored,
    )
    assert await tokens.read(response.access_token, {"login"})
    assert not await redis.exists(key)
