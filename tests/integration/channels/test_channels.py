"""CHN-A01—A16 的本单元职责：开通、隔离、凭据、状态和原子切换。"""

import asyncio
from datetime import timedelta

import pytest
from redis.exceptions import ConnectionError as RedisConnectionError
from sqlalchemy import event, select, text, update

from creativity_service.core.context import Scope
from creativity_service.core.primitives import ServiceError, new_id, unavailable, utcnow
from creativity_service.modules.channels.keys import secret_digest
from creativity_service.modules.channels.schemas import (
    ClientUpdate,
    DataScopeCreate,
    DataScopeUpdate,
    EnvironmentCreate,
    EnvironmentUpdate,
    KeyCreate,
    KeyRotate,
    TokenExchange,
)
from creativity_service.modules.channels.tables import metadata
from creativity_service.modules.iam.schemas import ChannelContextInput

from .conftest import channel_body, credential, login, provision

pytestmark = pytest.mark.integration


def headers(token):
    return {"Authorization": f"Bearer {token.access_token}"}


async def current_key(env, channel_id, key_id):
    return next(
        k for k in await env.services.keys.list_items(env.admin, channel_id) if k.key_id == key_id
    )


async def test_two_real_channels_token_binding_masks_and_index(
    channel_env, channel, service_identity
):
    env, rental = channel_env, service_identity
    playmate = await provision(env, "playmate", "playmate")
    other = await credential(env, playmate)
    assert rental.context.scope.channel_id != other.context.scope.channel_id
    assert rental.context.scope.environment == other.context.scope.environment == "test"
    assert rental.token.expires_at <= rental.key.key.expires_at
    token_record = await env.iam.authentication.tokens.read(rental.token.access_token, {"service"})
    assert (
        0
        < await env.redis.pttl(env.iam.authentication.tokens.token_key(token_record.token_digest))
        <= 1200000
    )
    async with env.engine.connect() as connection:
        row = (
            (
                await connection.execute(
                    select(metadata.tables["channel_keys"]).where(
                        metadata.tables["channel_keys"].c.channel_id == channel.channel.channel_id
                    )
                )
            )
            .mappings()
            .one()
        )
        assert row["secret_digest"] == secret_digest(rental.key.api_key)
        index = (
            (
                await connection.execute(
                    select(metadata.tables["key_identity_index"]).where(
                        metadata.tables["key_identity_index"].c.key_id == row["id"]
                    )
                )
            )
            .mappings()
            .one()
        )
        assert index["channel_id"] == "system"
        assert index["target_channel_id"] == channel.channel.channel_id
        assert rental.key.api_key not in str(row)
    response = await env.client.get(
        f"/admin/v1/channels/{channel.channel.channel_id}/keys", headers=headers(channel.token)
    )
    assert response.status_code == 200
    assert rental.key.api_key not in response.text and "secret_digest" not in response.text
    assert response.json()[0]["status_label"] == "启用"
    assert response.headers["Cache-Control"] == "no-store"
    assert "api_key" not in response.json()[0]
    await env.services.channels.initialize_system()
    assert len(await env.services.channels.list_items(env.admin)) == 2


async def test_no_context_override_or_direct_key_bearer(channel_env, channel, service_identity):
    env = channel_env
    for override in ({"channel_id": "foreign"}, {"environment": "prod"}, {"scopes": ["run:read"]}):
        result = await env.client.post(
            "/api/v1/auth/token", json={"api_key": service_identity.key.api_key, **override}
        )
        assert result.status_code == 422
        assert service_identity.key.api_key not in result.text
    result = await env.client.post(
        "/api/v1/auth/token",
        json={"api_key": service_identity.key.api_key},
        headers={"X-Channel-ID": "foreign", "X-Environment": "prod"},
    )
    assert result.status_code == 200
    auth = await env.iam.authentication.authenticate(result.json()["access_token"], "service")
    assert auth.scope == service_identity.context.scope
    with pytest.raises(ServiceError) as exc:
        await env.iam.authentication.authenticate(service_identity.key.api_key, "service")
    assert exc.value.status == 401
    with pytest.raises(ServiceError):
        await env.iam.authentication.revalidate(
            auth.model_copy(update={"scope": Scope(channel_id="foreign", environment="test")})
        )
    result = await env.client.get("/admin/v1/channels/system", headers=headers(env.admin_token))
    assert result.status_code == 404


async def test_concurrent_channel_code_and_first_member_rollback(channel_env, monkeypatch):
    env = channel_env
    results = await asyncio.gather(
        *(
            env.services.channels.create(env.admin, channel_body(env, " SAME-Code "))
            for _ in range(6)
        ),
        return_exceptions=True,
    )
    assert sum(not isinstance(r, Exception) for r in results) == 1
    assert all(
        isinstance(r, ServiceError) and r.status == 409 for r in results if isinstance(r, Exception)
    )
    before = await env.services.channels.list_items(env.admin)

    async def failure(*args, **kwargs):
        raise RuntimeError("首位管理员授权失败")

    monkeypatch.setattr(env.iam.access, "provision_first_member", failure)
    with pytest.raises(RuntimeError):
        await env.services.channels.create(env.admin, channel_body(env, "must-rollback"))
    assert await env.services.channels.list_items(env.admin) == before
    async with env.engine.connect() as connection:
        assert (
            await connection.scalar(
                text("SELECT count(*) FROM channel_code_index WHERE channel_code='must-rollback'")
            )
            == 0
        )
        assert await connection.scalar(text("SELECT count(*) FROM channel_memberships")) == 1
        assert await connection.scalar(text("SELECT count(*) FROM channel_environments")) == 1


async def test_key_index_failure_rolls_back_secret_and_audit(
    channel_env, channel, service_identity, monkeypatch
):
    env = channel_env
    before = await env.services.keys.list_items(env.admin, channel.channel.channel_id)

    async def failure(*args, **kwargs):
        raise RuntimeError("身份索引写入失败")

    monkeypatch.setattr(env.services.channels.repository, "add_index", failure)
    with pytest.raises(RuntimeError):
        await env.services.keys.create(
            channel.manager,
            channel.channel.channel_id,
            KeyCreate(
                name="失败凭据",
                client_id=service_identity.client.client_id,
                environment="test",
                scopes=["run:read"],
                expires_at=utcnow() + timedelta(hours=1),
            ),
        )
    assert await env.services.keys.list_items(env.admin, channel.channel.channel_id) == before
    async with env.engine.connect() as connection:
        assert await connection.scalar(text("SELECT count(*) FROM key_identity_index")) == 1
        assert (
            await connection.scalar(
                text("SELECT count(*) FROM audit_events WHERE action='key:create'")
            )
            == 1
        )


async def test_rotation_keeps_client_limits_overlap_and_revocation(
    channel_env, channel, service_identity
):
    env, identity, channel_id = channel_env, service_identity, channel.channel.channel_id
    old = await current_key(env, channel_id, identity.key.key.key_id)
    rotated = await env.services.keys.rotate(
        channel.manager,
        channel_id,
        old.key_id,
        KeyRotate(
            revision=old.revision, expires_at=utcnow() + timedelta(hours=1), overlap_seconds=60
        ),
    )
    assert rotated.key.key_id != old.key_id
    assert rotated.key.client_id == old.client_id
    assert rotated.overlap_until < old.expires_at
    await env.iam.authentication.authenticate(identity.token.access_token, "service")
    new_token = await env.services.keys.exchange(
        TokenExchange(api_key=rotated.api_key), new_id("request")
    )
    other = await credential(env, channel, "另一个服务")
    current = await current_key(env, channel_id, old.key_id)
    await env.services.keys.revoke(channel.manager, channel_id, old.key_id, current.revision)
    with pytest.raises(ServiceError) as exc:
        await env.iam.authentication.authenticate(identity.token.access_token, "service")
    assert exc.value.status == 401
    await env.iam.authentication.authenticate(new_token.access_token, "service")
    await env.iam.authentication.authenticate(other.token.access_token, "service")
    with pytest.raises(ServiceError):
        await env.services.keys.exchange(
            TokenExchange(api_key=identity.key.api_key), new_id("request")
        )


async def test_overlap_deadline_invalidates_old_token_without_redis_cleanup(
    channel_env, channel, service_identity
):
    env, identity, channel_id = channel_env, service_identity, channel.channel.channel_id
    old = await current_key(env, channel_id, identity.key.key.key_id)
    await env.services.keys.rotate(
        channel.manager,
        channel_id,
        old.key_id,
        KeyRotate(
            revision=old.revision, expires_at=utcnow() + timedelta(hours=1), overlap_seconds=30
        ),
    )
    table = metadata.tables["channel_keys"]
    async with env.engine.begin() as connection:
        await connection.execute(
            update(table)
            .where(table.c.channel_id == channel_id, table.c.id == old.key_id)
            .values(expires_at=utcnow() - timedelta(seconds=1))
        )
    token = await env.iam.authentication.tokens.read(identity.token.access_token, {"service"})
    assert token.expires_at > utcnow()
    with pytest.raises(ServiceError) as exc:
        await env.iam.authentication.authenticate(identity.token.access_token, "service")
    assert exc.value.status == 401


@pytest.mark.parametrize("target", ["environment", "domain", "client"])
async def test_state_disable_rejects_existing_token_and_worker(
    channel_env, channel, service_identity, target
):
    env, identity, channel_id = channel_env, service_identity, channel.channel.channel_id
    source = await env.iam.authentication.identity_source(identity.context)
    if target == "environment":
        env_view = (await env.services.channels.environments(env.admin, channel_id))[0]
        await env.services.channels.update_environment(
            env.admin,
            channel_id,
            "test",
            EnvironmentUpdate(revision=env_view.revision, status="DISABLED"),
        )
    elif target == "domain":
        await env.services.channels.update_data_scope(
            env.admin,
            channel_id,
            channel.domain.data_scope_id,
            DataScopeUpdate(revision=channel.domain.revision, status="DISABLED"),
        )
    else:
        await env.services.channels.update_client(
            channel.manager,
            channel_id,
            identity.client.client_id,
            ClientUpdate(revision=identity.client.revision, status="DISABLED"),
        )
    with pytest.raises(ServiceError):
        await env.iam.authentication.authenticate(identity.token.access_token, "service")
    with pytest.raises(ServiceError):
        await env.iam.authentication.revalidate(source.worker_context(new_id("request")))
    with pytest.raises(ServiceError):
        await env.services.keys.exchange(
            TokenExchange(api_key=identity.key.api_key), new_id("request")
        )


async def test_client_permission_shrink_affects_existing_token(
    channel_env, channel, service_identity
):
    env, identity = channel_env, service_identity
    await env.services.channels.update_client(
        channel.manager,
        channel.channel.channel_id,
        identity.client.client_id,
        ClientUpdate(revision=identity.client.revision, scopes=["run:read"]),
    )
    context = await env.iam.authentication.authenticate(identity.token.access_token, "service")
    current = await env.iam.authentication.service_identity(context)
    assert current.client_actions & current.key_actions == {"run:read"}
    with pytest.raises(ServiceError) as exc:
        await env.services.keys.create(
            channel.manager,
            channel.channel.channel_id,
            KeyCreate(
                name="超限权限",
                client_id=identity.client.client_id,
                environment="test",
                scopes=["run:create"],
                expires_at=utcnow() + timedelta(hours=1),
            ),
        )
    assert exc.value.status == 403


async def test_suspend_governance_resume_archive_and_durable_events(
    channel_env, channel, service_identity
):
    env, channel_id = channel_env, channel.channel.channel_id
    suspended = await env.services.lifecycle.change(
        channel.manager, channel_id, "suspend", channel.channel.revision
    )
    with pytest.raises(ServiceError) as exc:
        await env.iam.authentication.authenticate(service_identity.token.access_token, "service")
    assert exc.value.code == "CHANNEL_SUSPENDED"
    with pytest.raises(ServiceError):
        await env.iam.authentication.authenticate(channel.token.access_token, "management")
    detail = await env.client.get(
        f"/admin/v1/channels/{channel_id}", headers=headers(channel.token)
    )
    assert detail.status_code == 200 and detail.json()["status_label"] == "已暂停"
    session = await env.client.get("/admin/v1/auth/session", headers=headers(channel.token))
    assert session.status_code == 200
    assert "run:create" not in {a["action_key"] for a in session.json()["actions"]}
    resumed = await env.services.lifecycle.change(
        channel.manager, channel_id, "resume", suspended.revision
    )
    await env.iam.authentication.authenticate(service_identity.token.access_token, "service")
    preview = await env.services.lifecycle.preview(env.admin, channel_id, "archive")
    assert not preview.can_execute and preview.active_keys == 1
    # 当前夹具已安装任务表；缺少真实检查器时须拒绝，不能再假定未安装 11。
    with pytest.raises(ServiceError) as missing_guard:
        await env.services.lifecycle.change(env.admin, channel_id, "archive", resumed.revision)
    assert missing_guard.value.status == 503

    class EmptyTaskFixture:
        def keys(self, channel_id):
            return []

        async def unfinished(self, connection, channel_id):
            # 此用例没有任务，明确核对测试前提后仅验证渠道状态与事件。
            count = await connection.scalar(
                text("SELECT count(*) FROM runs WHERE channel_id = :channel_id"),
                {"channel_id": channel_id},
            )
            assert count == 0
            return count

    env.services.lifecycle.register_tasks(EmptyTaskFixture())
    with pytest.raises(ServiceError) as exc:
        await env.services.lifecycle.change(env.admin, channel_id, "archive", resumed.revision)
    assert exc.value.status == 409
    key = await current_key(env, channel_id, service_identity.key.key.key_id)
    await env.services.keys.revoke(env.admin, channel_id, key.key_id, key.revision)
    archived = await env.services.lifecycle.change(
        env.admin, channel_id, "archive", resumed.revision
    )
    assert archived.archived_at and archived.status == "ARCHIVED"
    pending = await env.services.lifecycle.pending(channel_id, "retention")
    archived_event = next(e for e in pending if e.event_type == "channel:archive")
    assert archived_event.channel_id == channel_id
    await env.services.lifecycle.acknowledge(channel_id, archived_event.event_id, "retention")
    await env.services.lifecycle.acknowledge(channel_id, archived_event.event_id, "retention")
    assert archived_event not in await env.services.lifecycle.pending(channel_id, "retention")
    assert archived_event in await env.services.lifecycle.pending(channel_id, "runs")
    with pytest.raises(ServiceError):
        await env.services.lifecycle.change(env.admin, channel_id, "resume", archived.revision)


async def test_mapping_concurrency_environment_ownership_and_new_workspace(channel_env):
    env = channel_env
    channel = await provision(env, "playmate", "playmate")
    channel_id = channel.channel.channel_id
    body = DataScopeCreate(
        name="俱乐部乙",
        environment="test",
        external_scope_type="club",
        external_scope_id="same-club",
    )
    results = await asyncio.gather(
        *(env.services.channels.create_data_scope(env.admin, channel_id, body) for _ in range(6)),
        return_exceptions=True,
    )
    assert sum(not isinstance(r, Exception) for r in results) == 1
    assert all(r.status == 409 for r in results if isinstance(r, ServiceError))
    await env.services.channels.create_environment(
        env.admin, channel_id, EnvironmentCreate(environment="prod", name="生产")
    )
    domain = await env.services.channels.create_data_scope(
        env.admin,
        channel_id,
        body.model_copy(update={"environment": "prod", "administrator_id": env.user_id}),
    )
    _, session = await login(env)
    token = await env.iam.sessions.enter(
        session,
        ChannelContextInput(
            channel_id=channel_id, environment="prod", data_scope_id=domain.data_scope_id
        ),
    )
    manager = await env.iam.authentication.admin_session(token.access_token, new_id("request"))
    assert (await env.services.channels.detail(manager, channel_id)).channel_id == channel_id
    path = f"/admin/v1/channels/{channel_id}/data-scopes/{domain.data_scope_id}"
    result = await env.client.patch(
        path,
        headers=headers(token),
        json={"revision": domain.revision, "external_scope_id": "moved"},
    )
    assert result.status_code == 422
    assert (
        await env.client.patch(
            f"/admin/v1/channels/{channel_id}/environments/prod",
            headers=headers(token),
            json={"revision": 1, "environment": "test"},
        )
    ).status_code == 422


async def test_cross_channel_reference_and_governance_does_not_grant_data(
    channel_env, channel, service_identity
):
    env = channel_env
    other = await provision(env, "playmate", "playmate")
    with pytest.raises(ServiceError) as exc:
        await env.services.channels.detail(channel.manager, other.channel.channel_id)
    assert exc.value.status == 404
    with pytest.raises(ServiceError) as exc:
        await env.services.keys.create(
            other.manager,
            other.channel.channel_id,
            KeyCreate(
                name="跨渠道",
                client_id=service_identity.client.client_id,
                environment="test",
                scopes=["run:read"],
                expires_at=utcnow() + timedelta(hours=1),
            ),
        )
    assert exc.value.status == 404
    with pytest.raises(ServiceError) as exc:
        await env.services.keys.create(
            env.admin,
            channel.channel.channel_id,
            KeyCreate(
                name="治理越权",
                client_id=service_identity.client.client_id,
                environment="test",
                scopes=["run:read"],
                expires_at=utcnow() + timedelta(hours=1),
            ),
        )
    assert exc.value.status == 403
    with pytest.raises(ServiceError):
        await env.iam.authentication.authenticate(env.admin_token.access_token, "management")


async def test_workspace_atomic_switch_failure_and_session_indexes(
    channel_env, channel, monkeypatch
):
    env = channel_env
    other = await provision(env, "playmate", "playmate")
    body = ChannelContextInput(
        channel_id=other.channel.channel_id,
        environment="test",
        data_scope_id=other.domain.data_scope_id,
    )
    original = env.iam.authentication.tokens._eval

    async def fail_issue(script, *args):
        if "redis.call('SET'" in script:
            raise RedisConnectionError("认证存储中断")
        return await original(script, *args)

    monkeypatch.setattr(env.iam.authentication.tokens, "_eval", fail_issue)
    with pytest.raises(ServiceError) as exc:
        await env.iam.sessions.enter(channel.manager, body)
    assert exc.value.status == 503
    await env.iam.authentication.authenticate(channel.token.access_token, "management")
    monkeypatch.setattr(env.iam.authentication.tokens, "_eval", original)
    results = await asyncio.gather(
        *(env.iam.sessions.enter(channel.manager, body) for _ in range(2)), return_exceptions=True
    )
    assert sum(not isinstance(r, Exception) for r in results) == 1
    with pytest.raises(ServiceError):
        await env.iam.authentication.authenticate(channel.token.access_token, "management")
    assert (
        await env.redis.zcard(
            env.iam.authentication.tokens.index_key(
                channel.channel.channel_id, "member", env.user_id
            )
        )
        == 0
    )
    assert channel.manager.context.scope.channel_id == channel.channel.channel_id


async def test_revocation_storage_failure_and_index_tampering_fail_closed(
    channel_env, channel, service_identity, monkeypatch
):
    env, identity, channel_id = channel_env, service_identity, channel.channel.channel_id

    async def fail(*args):
        raise unavailable("撤销存储")

    original = env.iam.authentication.tokens.revoke
    monkeypatch.setattr(env.iam.authentication.tokens, "revoke", fail)
    key = await current_key(env, channel_id, identity.key.key.key_id)
    await env.services.keys.revoke(channel.manager, channel_id, key.key_id, key.revision)
    with pytest.raises(ServiceError) as exc:
        await env.iam.authentication.authenticate(identity.token.access_token, "service")
    assert exc.value.status == 401
    monkeypatch.setattr(env.iam.authentication.tokens, "revoke", original)
    assert (await env.iam.revocations.reconcile())[1] == 0
    table = metadata.tables["key_identity_index"]
    async with env.engine.begin() as connection:
        await connection.execute(
            update(table).where(table.c.key_id == key.key_id).values(target_channel_id="foreign")
        )
    with pytest.raises(ServiceError) as exc:
        await env.services.keys.exchange(
            TokenExchange(api_key=identity.key.api_key), new_id("request")
        )
    assert exc.value.status == 401


async def test_identity_lookup_queries_always_scoped(channel_env, service_identity):
    env = channel_env
    queries = []

    def capture(connection, cursor, statement, parameters, context, executemany):
        if statement.lstrip().upper().startswith("SELECT"):
            queries.append(statement)

    event.listen(env.engine.sync_engine, "before_cursor_execute", capture)
    try:
        await env.services.keys.exchange(
            TokenExchange(api_key=service_identity.key.api_key), new_id("request")
        )
    finally:
        event.remove(env.engine.sync_engine, "before_cursor_execute", capture)
    assert "key_identity_index" in queries[0]
    assert "key_lookup_digest =" in queries[0] and "channel_id =" in queries[0]
    for query in queries:
        if any(
            f"FROM {table}" in query
            for table in (
                "channel_keys",
                "channels",
                "service_clients",
                "data_scopes",
                "channel_environments",
            )
        ):
            assert "channel_id =" in query.split("WHERE", 1)[1]


async def test_archive_task_guard_and_no_fabricated_usage(channel_env, channel):
    env, channel_id = channel_env, channel.channel.channel_id

    class Guard:
        def keys(self, channel_id):
            return []

        async def unfinished(self, connection, channel_id):
            return 2

    env.services.lifecycle.register_tasks(Guard())
    impact = await env.services.lifecycle.preview(env.admin, channel_id, "archive")
    assert not impact.can_execute and impact.unfinished_tasks == 2
    with pytest.raises(ServiceError):
        await env.services.lifecycle.change(
            env.admin, channel_id, "archive", channel.channel.revision
        )
    response = await env.client.get(
        f"/admin/v1/channels/{channel_id}/usage",
        headers=headers(channel.token),
        params={"start_at": "2026-10-01T00:00:00Z", "end_at": "2026-10-02T00:00:00Z"},
    )
    assert response.status_code == 503


async def test_real_environment_scope_pairs_work_with_iam_delegation(channel_env, channel):
    from creativity_service.modules.iam.schemas import AccountCreate, GrantInput, MembershipInput

    env, channel_id = channel_env, channel.channel.channel_id
    await env.services.channels.create_environment(
        env.admin, channel_id, EnvironmentCreate(environment="prod", name="生产")
    )
    prod = await env.services.channels.create_data_scope(
        env.admin,
        channel_id,
        DataScopeCreate(
            name="生产默认域",
            environment="prod",
            external_scope_type="default",
            external_scope_id="default",
            administrator_id=env.user_id,
        ),
    )
    _, session = await login(env)
    token = await env.iam.sessions.enter(
        session,
        ChannelContextInput(
            channel_id=channel_id, environment="test", data_scope_id=channel.domain.data_scope_id
        ),
    )
    manager = await env.iam.authentication.admin_session(token.access_token, new_id("request"))
    target = await env.iam.accounts.create(
        env.admin,
        AccountCreate(
            login_name="scoped-auditor",
            display_name="审计员",
            initial_password="Auditor-password-1234",
        ),
    )
    domains = [channel.domain.data_scope_id, prod.data_scope_id]
    member = await env.iam.access.put_member(
        manager,
        channel_id,
        target.user_id,
        MembershipInput(roles=["auditor"], environments=["test", "prod"], data_scopes=domains),
    )
    assert member.data_scopes == domains
    grant = await env.iam.access.put_grant(
        manager,
        channel_id,
        "auditor-grant",
        GrantInput(
            grantee_type="account",
            grantee_id=target.user_id,
            resource_type="channel",
            resource_id=channel_id,
            allowed_actions=["run:read"],
            environments=["test", "prod"],
            data_scopes=domains,
        ),
    )
    assert grant.data_scopes == domains
    assert len(await env.iam.sessions.channels(manager)) == 2


async def test_full_channel_governance_requires_all_data_domains(channel_env, channel):
    env, channel_id = channel_env, channel.channel.channel_id
    await env.services.channels.create_environment(
        env.admin, channel_id, EnvironmentCreate(environment="prod", name="生产")
    )
    await env.services.channels.create_data_scope(
        env.admin,
        channel_id,
        DataScopeCreate(
            name="未授权生产域",
            environment="prod",
            external_scope_type="default",
            external_scope_id="default",
        ),
    )
    with pytest.raises(ServiceError) as exc:
        await env.services.lifecycle.change(
            channel.manager, channel_id, "suspend", channel.channel.revision
        )
    assert exc.value.status == 404
    assert (await env.services.channels.detail(env.admin, channel_id)).status == "ACTIVE"


async def test_usage_delegates_explicit_authorized_scopes_and_records_platform_range(
    channel_env, channel
):
    from creativity_service.modules.channels.schemas import UsageView

    env, channel_id = channel_env, channel.channel.channel_id
    called = []

    class Usage:
        async def query(self, target_channel_id, scopes, query):
            called.append((target_channel_id, scopes))
            return UsageView(
                channel_id=target_channel_id,
                channel_name="租号渠道",
                start_at=query.start_at,
                end_at=query.end_at,
                calls=3,
                input_tokens=20,
                output_tokens=10,
                costs=[],
            )

    env.services.channels.usage_reader = Usage()
    result = await env.services.channels.platform_usage(
        env.admin, [channel_id], "2026-10-01T00:00:00Z", "2026-10-02T00:00:00Z"
    )
    assert result[0].calls == 3
    assert called == [
        (
            channel_id,
            [
                Scope(
                    channel_id=channel_id,
                    environment="test",
                    data_scope_id=channel.domain.data_scope_id,
                )
            ],
        )
    ]
    async with env.engine.connect() as connection:
        summary = await connection.scalar(
            text(
                "SELECT summary FROM audit_events "
                "WHERE channel_id='system' AND action='usage:platform'"
            )
        )
    assert summary["channel_ids"] == [channel_id]
    response = await env.client.get(
        "/admin/v1/platform/usage",
        params={"start_at": "2026-10-01T00:00:00Z", "end_at": "2026-10-02T00:00:00Z"},
        headers=headers(env.admin_token),
    )
    assert response.status_code == 422


async def test_http_channel_key_creation_rotation_audit_and_invalid_references(
    channel_env, channel, service_identity
):
    env, channel_id, identity = channel_env, channel.channel.channel_id, service_identity
    base = f"/admin/v1/channels/{channel_id}"
    response = await env.client.get(base + "/overview", headers=headers(channel.token))
    assert response.status_code == 200 and response.json()["resource_references"] is None
    payload = {
        "name": "接口凭据",
        "client_id": identity.client.client_id,
        "environment": "test",
        "scopes": ["run:read"],
        "expires_at": (utcnow() + timedelta(hours=1)).isoformat(),
    }
    response = await env.client.post(
        base + "/keys", json={**payload, "environment": "prod"}, headers=headers(channel.token)
    )
    assert response.status_code == 404
    response = await env.client.post(base + "/keys", json=payload, headers=headers(channel.token))
    assert response.status_code == 201
    created = response.json()
    assert response.headers["Cache-Control"] == "no-store"
    response = await env.client.post(
        base + "/keys/" + created["key"]["key_id"] + "/rotate",
        headers=headers(channel.token),
        json={
            "revision": created["key"]["revision"],
            "expires_at": payload["expires_at"],
            "overlap_seconds": 0,
        },
    )
    assert (
        response.status_code == 201
        and response.json()["key"]["client_id"] == identity.client.client_id
    )
    audit = await env.client.get(base + "/audit-events", headers=headers(channel.token))
    assert audit.status_code == 200
    assert "轮换接入 Key" in {a["action_name"] for a in audit.json()}
    assert created["api_key"] not in audit.text and response.json()["api_key"] not in audit.text
    assert "租号渠道" in {a["target_name"] for a in audit.json()}


async def test_audit_records_real_changed_fields(channel_env, channel):
    from creativity_service.modules.channels.schemas import ChannelUpdate

    env, channel_id = channel_env, channel.channel.channel_id
    updated = await env.services.channels.update(
        channel.manager,
        channel_id,
        ChannelUpdate(revision=channel.channel.revision, name="新渠道名称"),
    )
    assert updated.name == "新渠道名称"
    audits = await env.services.channels.audit(channel.manager, channel_id)
    changed = next(a for a in audits if a.action == "channel:update")
    assert changed.changed_fields == ["名称"]
