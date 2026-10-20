"""已吊销渠道凭据删除的状态、隔离、并发与历史保留验证。"""

import asyncio
from datetime import timedelta

import pytest
from sqlalchemy import select, update

from creativity_service.core.database.tables import metadata as core_metadata
from creativity_service.core.primitives import ServiceError, utcnow
from creativity_service.modules.channels.repositories import one, rows
from creativity_service.modules.channels.schemas import EnvironmentCreate
from creativity_service.modules.channels.tables import metadata
from creativity_service.modules.iam.schemas import ChannelContextInput

from .conftest import credential, login, provision

pytestmark = pytest.mark.integration


async def delete(env, token, channel_id, key_id, revision):
    return await env.client.request(
        "DELETE",
        f"/admin/v1/channels/{channel_id}/keys/{key_id}",
        headers={"Authorization": f"Bearer {token.access_token}"},
        json={"revision": revision},
    )


@pytest.mark.parametrize("governance", [False, True])
async def test_delete_revoked_key_preserves_history_and_other_credentials(
    channel_env, channel, service_identity, governance
):
    env, channel_id = channel_env, channel.channel.channel_id
    key = service_identity.key.key
    other = await credential(env, channel, "另一接入服务")
    revoked = await env.services.keys.revoke(channel.manager, channel_id, key.key_id, key.revision)
    token = env.admin_token if governance else channel.token
    stale = await delete(env, token, channel_id, key.key_id, key.revision)
    assert stale.status_code == 409
    assert stale.json()["error"]["code"] == "REVISION_CONFLICT"
    response = await delete(env, token, channel_id, key.key_id, revoked.revision)
    assert response.status_code == 204, response.text
    assert response.headers["Cache-Control"] == "no-store"
    listed = await env.services.keys.list_items(env.admin, channel_id)
    assert {item.key_id for item in listed} == {other.key.key.key_id}
    async with env.engine.connect() as connection:
        assert await one(connection, "channel_keys", channel_id, id=key.key_id) is None
        stored = await one(
            connection, "channel_keys", channel_id, id=key.key_id, include_deleted=True
        )
        assert stored["is_deleted"] is True and stored["status"] == "REVOKED"
        assert stored["revision"] == revoked.revision + 1
        assert stored["client_id"] == key.client_id
        events = await rows(
            connection, "channel_lifecycle_events", channel_id, target_id=key.key_id
        )
        assert {event["event_type"] for event in events} == {
            "key:create",
            "key:revoke",
            "key:delete",
        }
        audits = core_metadata.tables["audit_events"]
        actions = set(
            await connection.scalars(
                select(audits.c.action).where(
                    audits.c.channel_id == channel_id, audits.c.target_id == key.key_id
                )
            )
        )
        assert {"key:create", "key:revoke", "key:delete"} <= actions
    assert (await delete(env, token, channel_id, key.key_id, revoked.revision)).status_code == 404
    exchange = await env.client.post(
        "/api/v1/auth/token", json={"key_id": key.key_id, "secret": service_identity.key.api_key}
    )
    assert exchange.status_code == 401
    with pytest.raises(ServiceError):
        await env.iam.authentication.authenticate(service_identity.token.access_token, "service")
    await env.iam.authentication.authenticate(other.token.access_token, "service")


@pytest.mark.parametrize("expired", [False, True])
async def test_active_or_expired_key_cannot_be_deleted(
    channel_env, channel, service_identity, expired
):
    env, channel_id, key = channel_env, channel.channel.channel_id, service_identity.key.key
    if expired:
        table = metadata.tables["channel_keys"]
        async with env.engine.begin() as connection:
            await connection.execute(
                update(table)
                .where(table.c.channel_id == channel_id, table.c.id == key.key_id)
                .values(expires_at=utcnow() - timedelta(seconds=1))
            )
    response = await delete(env, channel.token, channel_id, key.key_id, key.revision)
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "INVALID_STATE"
    listed = await env.services.keys.list_items(env.admin, channel_id)
    assert [item.key_id for item in listed] == [key.key_id]


async def test_key_deletion_rejects_cross_channel_and_environment(
    channel_env, channel, service_identity
):
    env, channel_id, key = channel_env, channel.channel.channel_id, service_identity.key.key
    revoked = await env.services.keys.revoke(channel.manager, channel_id, key.key_id, key.revision)
    other = await provision(env, "playmate", "club")
    response = await delete(env, other.token, channel_id, key.key_id, revoked.revision)
    assert response.status_code == 404
    response = await delete(
        env, other.token, other.channel.channel_id, key.key_id, revoked.revision
    )
    assert response.status_code == 404
    await env.services.channels.create_environment(
        env.admin, channel_id, EnvironmentCreate(environment="prod", name="生产")
    )
    _, admin = await login(env)
    production = await env.iam.sessions.enter(
        admin, ChannelContextInput(channel_id=channel_id, environment="prod")
    )
    response = await delete(env, production, channel_id, key.key_id, revoked.revision)
    assert response.status_code == 404
    assert len(await env.services.keys.list_items(env.admin, channel_id)) == 1


async def test_concurrent_key_deletion_commits_one_audit(channel_env, channel, service_identity):
    env, channel_id, key = channel_env, channel.channel.channel_id, service_identity.key.key
    revoked = await env.services.keys.revoke(channel.manager, channel_id, key.key_id, key.revision)
    results = await asyncio.gather(
        *(delete(env, channel.token, channel_id, key.key_id, revoked.revision) for _ in range(3))
    )
    assert sorted(response.status_code for response in results) == [204, 404, 404]
    async with env.engine.connect() as connection:
        events = await rows(
            connection,
            "channel_lifecycle_events",
            channel_id,
            target_id=key.key_id,
            event_type="key:delete",
        )
        assert len(events) == 1
