"""IAM-A08/A10：滑动期限、用途隔离、切换竞争与撤销索引。"""

import asyncio
from datetime import timedelta

import pytest

from creativity_service.core.auth.tokens import RENEW_SCRIPT
from creativity_service.core.auth.types import Revocation, TokenRecord
from creativity_service.core.context import AuthContext, Scope
from creativity_service.core.primitives import ServiceError, new_id, utcnow

from .conftest import enter, login, provision

pytestmark = pytest.mark.integration


@pytest.mark.parametrize("identity", ["admin", "manager"])
async def test_management_use_renews_eight_hours_and_revocation_indexes(
    iam_env, admin, manager, identity
):
    response, session = admin if identity == "admin" else manager
    iam, client, _, _, redis = iam_env
    tokens = iam.authentication.tokens
    key = tokens.token_key(session.token.token_digest)
    assert response.expires_in == 28_800
    assert 28_790_000 < await redis.pttl(key) <= 28_800_000
    # 模拟上线前签发的 ISO 时间串和即将到期的旧会话，下一次真实请求即可续到八小时。
    old = session.token.model_copy(update={"expires_at": utcnow() + timedelta(seconds=60)})
    await redis.set(key, old.model_dump_json(), px=60_000)
    for index in old.index_keys:
        await redis.zadd(index, {old.token_digest: old.expires_at.timestamp()})
        await redis.expire(index, 60)
    headers = {"Authorization": f"Bearer {response.access_token}"}
    result = await client.get("/admin/v1/auth/session", headers=headers)
    assert result.status_code == 200, result.text
    current = await tokens.read(response.access_token, {old.purpose})
    assert current.expires_at > old.expires_at + timedelta(hours=7)
    assert 28_790_000 < await redis.pttl(key) <= 28_800_000
    for index in old.index_keys:
        assert await redis.zscore(index, old.token_digest) == pytest.approx(
            current.expires_at.timestamp(), abs=0.001, rel=0
        )
        assert 28_790_000 < await redis.pttl(index) <= 28_801_000
    # 再次使用必须继续延后期限，返回的会话到期时间与存储一致。
    await asyncio.sleep(0.01)
    result = await client.get("/admin/v1/auth/session", headers=headers)
    assert result.status_code == 200
    latest = await tokens.read(response.access_token, {old.purpose})
    assert latest.expires_at > current.expires_at
    assert result.json()["expires_at"] == latest.expires_at.isoformat().replace("+00:00", "Z")
    await tokens.revoke(
        Revocation(
            id=new_id("revoke"),
            channel_id="system",
            kind="account",
            target_id=old.principal_id,
            cutoff_at=utcnow(),
        )
    )
    assert not await redis.exists(key)


async def test_token_ttl_deletion_and_expiry_cannot_be_renewed(iam_env, admin):
    iam, client, _, _, redis = iam_env
    tokens = iam.authentication.tokens
    key = tokens.token_key(tokens.digest(admin[0].access_token))
    assert admin[0].access_token.encode() not in await redis.get(key)
    headers = {"Authorization": f"Bearer {admin[0].access_token}"}
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


async def test_expired_record_with_live_redis_ttl_cannot_be_renewed(iam_env, admin):
    iam, client, _, _, redis = iam_env
    tokens = iam.authentication.tokens
    key = tokens.token_key(admin[1].token.token_digest)
    expired = admin[1].token.model_copy(
        update={
            "issued_at": utcnow() - timedelta(hours=9),
            "expires_at": utcnow() - timedelta(seconds=1),
        }
    )
    await redis.set(key, expired.model_dump_json(), px=60_000)
    response = await client.get(
        "/admin/v1/auth/session",
        headers={
            "Authorization": f"Bearer {admin[0].access_token}",
        },
    )
    assert response.status_code == 401
    assert await redis.pttl(key) <= 60_000


async def test_concurrent_renewals_preserve_binding_and_switch_single_winner(iam_env, admin):
    iam, _, _, _, redis = iam_env
    tokens = iam.authentication.tokens
    stale = admin[1].token
    renewed = await asyncio.gather(*(tokens.renew(stale) for _ in range(8)))
    current = await tokens.read(admin[0].access_token, {"login"})
    assert current.expires_at == max(record.expires_at for record in renewed)
    assert all(record.same_session(stale) for record in renewed)
    await iam.authentication.revalidate_admin(admin[1])
    # 已有请求拿着续时前的快照切换，不能误报会话变更；两次切换仍只允许一次成功。
    results = await asyncio.gather(
        *(
            tokens.issue(
                purpose="login",
                principal_id=stale.principal_id,
                credential_version=stale.credential_version,
                replace=stale,
            )
            for _ in range(2)
        ),
        return_exceptions=True,
    )
    assert sum(isinstance(result, tuple) for result in results) == 1
    assert sum(isinstance(result, ServiceError) for result in results) == 1
    with pytest.raises(ServiceError) as exc:
        await tokens.renew(stale)
    assert exc.value.status == 401
    assert not await redis.exists(tokens.token_key(stale.token_digest))


async def test_revoke_between_validation_and_renewal_does_not_restore_session(
    iam_env,
    admin,
    monkeypatch,
):
    iam, client, _, _, redis = iam_env
    tokens = iam.authentication.tokens
    original_renew = tokens.renew
    validated, proceed = asyncio.Event(), asyncio.Event()

    async def paused_renew(record):
        validated.set()
        await proceed.wait()
        return await original_renew(record)

    monkeypatch.setattr(tokens, "renew", paused_renew)
    pending = asyncio.create_task(
        client.get(
            "/admin/v1/auth/session",
            headers={
                "Authorization": f"Bearer {admin[0].access_token}",
            },
        )
    )
    await asyncio.wait_for(validated.wait(), 5)
    await tokens.revoke(
        Revocation(
            id=new_id("revoke"),
            channel_id="system",
            kind="account",
            target_id=admin[1].account.id,
            cutoff_at=utcnow(),
        )
    )
    proceed.set()
    assert (await pending).status_code == 401
    assert not await redis.exists(tokens.token_key(admin[1].token.token_digest))


async def test_renewal_failure_is_503(iam_env, admin, monkeypatch):
    from redis.exceptions import ConnectionError as RedisConnectionError

    iam, client, _, _, _ = iam_env
    original_eval = iam.authentication.tokens._eval

    async def fail_renewal(script, count, *args):
        if script == RENEW_SCRIPT:
            raise RedisConnectionError()
        return await original_eval(script, count, *args)

    monkeypatch.setattr(iam.authentication.tokens, "_eval", fail_renewal)
    response = await client.get(
        "/admin/v1/auth/session",
        headers={
            "Authorization": f"Bearer {admin[0].access_token}",
        },
    )
    assert response.status_code == 503


@pytest.mark.parametrize("failure", ["no_ttl", "expired", "upstream_expired"])
async def test_deadline_rechecked_between_validation_and_renewal(iam_env, admin, failure):
    iam, _, _, _, redis = iam_env
    tokens = iam.authentication.tokens
    record = admin[1].token
    key = tokens.token_key(record.token_digest)
    if failure == "no_ttl":
        await redis.persist(key)
    elif failure == "expired":
        # 模拟已读完身份后到期；存储时间戳与残留 TTL 不一致也必须拒绝。
        import json

        raw = json.loads(await redis.get(key))
        raw["expires_at"] = (utcnow() - timedelta(seconds=1)).timestamp()
        await redis.set(key, json.dumps(raw), px=60_000)
    else:
        record = record.model_copy(update={"upstream_expires_at": utcnow() - timedelta(seconds=1)})
        await redis.set(key, record.model_dump_json(), px=60_000)
    with pytest.raises(ServiceError) as exc:
        await tokens.renew(record)
    assert exc.value.status == 401
    assert await redis.pttl(key) <= 60_000


async def test_external_deadline_caps_renewal_and_boundary_use_renews(iam_env, manager):
    iam, _, _, _, redis = iam_env
    tokens = iam.authentication.tokens
    record = manager[1].token
    key = tokens.token_key(record.token_digest)
    cap = utcnow() + timedelta(minutes=3)
    limited = record.model_copy(
        update={
            "upstream_expires_at": cap,
            "identity_channel_id": record.channel_id,
            "expires_at": utcnow() + timedelta(seconds=30),
        }
    )
    await redis.set(key, limited.model_dump_json(), px=30_000)
    # SSE 与下载边界通过同一复核入口续时，无需前端定时刷新。
    await iam.authentication.revalidate(manager[1].context)
    renewed = TokenRecord.model_validate_json(await redis.get(key))
    assert abs((renewed.expires_at - cap).total_seconds()) < 0.001
    assert 170_000 < await redis.pttl(key) <= 180_000


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
    # 业务服务 Token 保持 Key 约束下的固定期限，管理续时不会延长它。
    service_current = await iam.authentication.tokens.read(service.access_token, {"service"})
    assert service_current.expires_at == record.expires_at
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
