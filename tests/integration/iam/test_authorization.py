"""IAM-A04/A05/A06/A07/A09/A11/A13：当前范围与授权不可自增。"""

import asyncio

import pytest

from creativity_service.core.context import Scope
from creativity_service.core.database import transaction
from creativity_service.core.locking import record_key
from creativity_service.core.primitives import ServiceError, new_id
from creativity_service.modules.iam.repositories import one, policy_key, rows, save
from creativity_service.modules.iam.schemas import GrantInput, MembershipInput, PasswordChange

from .conftest import PASSWORD, create_user, enter, login, provision

pytestmark = pytest.mark.integration


async def builder(iam, admin, manager, channel="channel_a"):
    account, (_, session) = await create_user(iam, admin)
    await iam.access.put_member(
        manager,
        channel,
        account.user_id,
        MembershipInput(roles=["builder"], environments=["test", "prod"]),
    )
    await iam.access.put_grant(
        manager,
        channel,
        "builder_version",
        GrantInput(
            grantee_type="account",
            grantee_id=account.user_id,
            resource_type="version",
            resource_id="version_a",
            allowed_actions=["version:edit", "version:read"],
            environments=["test", "prod"],
        ),
    )
    return account, await enter(iam, session, channel)


async def test_builder_can_edit_but_not_publish_self_grant_or_cross_scope(iam_env, admin, manager):
    iam, client, _, _, _ = iam_env
    account, (response, session) = await builder(iam, admin[1], manager[1])
    await iam.authorization.require(session.context, "version:edit", "version_a")
    for action in ("release:publish", "data:export", "data:read_sensitive"):
        with pytest.raises(ServiceError) as exc:
            await iam.authorization.boundary(session.context, action, "version", "version_a")
        assert exc.value.status == 403
    with pytest.raises(ServiceError) as exc:
        await iam.access.put_member(
            session,
            "channel_a",
            account.user_id,
            MembershipInput(roles=["channel_admin"], environments=["test"]),
        )
    assert exc.value.status == 403
    headers = {
        "Authorization": f"Bearer {response.access_token}",
        "X-Role": "platform_admin",
        "X-Channel-ID": "channel_b",
    }
    assert (await client.get("/admin/v1/accounts", headers=headers)).status_code == 403
    assert (
        await client.get("/admin/v1/channels/channel_b/members", headers=headers)
    ).status_code == 404
    response = await client.post(
        "/admin/v1/auth/channel-context",
        headers=headers,
        json={"channel_id": "channel_a", "environment": "dev"},
    )
    assert response.status_code == 404
    view = await iam.sessions.view(session)
    assert "release:publish" not in {action.action_key for action in view.actions}
    assert "accounts" not in {item.navigation_key for item in view.navigation}


async def test_governance_is_not_business_content_permission(iam_env, admin, manager):
    iam, client, _, _, _ = iam_env
    assert (
        await client.get(
            "/admin/v1/accounts", headers={"Authorization": f"Bearer {admin[0].access_token}"}
        )
    ).status_code == 200
    with pytest.raises(ServiceError):
        await iam.authentication.authenticate(admin[0].access_token, "management")
    context = manager[1].context
    await iam.authorization.boundary(context, "audit:read", "channel", "channel_a")
    for action, resource_type in (
        ("run:content", "run"),
        ("snapshot:read", "snapshot"),
        ("artifact:download", "artifact"),
    ):
        with pytest.raises(ServiceError) as exc:
            await iam.authorization.boundary(context, action, resource_type, "resource_a")
        assert exc.value.status == 403
    system_events = await iam.audit.query(admin[1])
    channel_events = await iam.audit.query(manager[1])
    assert "account:initialize" in {event.action for event in system_events}
    assert "account:initialize" not in {event.action for event in channel_events}
    assert "auth:channel-context" in {event.action for event in channel_events}


async def test_membership_removal_is_channel_local_and_stops_worker(iam_env, admin, manager):
    iam, _, _, _, _ = iam_env
    account, (token_a, session_a) = await builder(iam, admin[1], manager[1])
    await provision(iam, admin[1], "channel_b", account.user_id)
    _, temporary = await login(iam, account.login_name)
    token_b, session_b = await enter(iam, temporary, "channel_b")
    worker = session_a.context.model_copy(
        update={"principal_type": "worker", "session_id": None, "token_digest": None}
    )
    member = await iam.accounts.repository.membership("channel_a", account.user_id)
    await iam.access.remove_member(manager[1], "channel_a", account.user_id, member.revision)
    with pytest.raises(ServiceError):
        await iam.authentication.authenticate(token_a.access_token, "management")
    with pytest.raises(ServiceError):
        await iam.authorization.require(worker, "version:edit", "version_a")
    await iam.authentication.authenticate(token_b.access_token, "management")
    assert (await iam.sessions.view(session_b)).workspace.channel_id == "channel_b"
    assert (
        await iam.accounts.repository.membership("channel_b", account.user_id)
    ).status == "ACTIVE"


async def test_revoking_grant_stops_next_boundary_without_relying_on_menu(iam_env, admin, manager):
    iam, _, _, _, _ = iam_env
    _, (_, session) = await builder(iam, admin[1], manager[1])
    await iam.authorization.require(session.context, "version:edit", "version_a")
    worker = session.context.model_copy(
        update={"principal_type": "worker", "session_id": None, "token_digest": None}
    )
    await iam.access.revoke_grant(manager[1], "channel_a", "builder_version", 1)
    for context in (session.context, worker):
        with pytest.raises(ServiceError) as exc:
            await iam.authorization.boundary(context, "version:edit", "version", "version_a")
        assert exc.value.status == 403
    assert "builder_version" not in {
        item.grant_id for item in await iam.access.list_grants(manager[1], "channel_a")
    }


async def test_concurrent_members_and_grants_accept_once_and_revision_conflicts(
    iam_env, admin, manager
):
    iam, _, _, engine, _ = iam_env
    account, _ = await create_user(iam, admin[1])

    async def add():
        return await iam.access.put_member(
            manager[1],
            "channel_a",
            account.user_id,
            MembershipInput(roles=["builder"], environments=["test"]),
        )

    results = await asyncio.gather(*(add() for _ in range(8)), return_exceptions=True)
    assert sum(not isinstance(r, Exception) for r in results) == 1
    assert all(r.status == 409 for r in results if isinstance(r, ServiceError))
    async with engine.connect() as connection:
        assert (
            len(await rows(connection, "channel_memberships", "channel_a", user_id=account.user_id))
            == 1
        )

    async def grant(index):
        return await iam.access.put_grant(
            manager[1],
            "channel_a",
            f"grant_{index}",
            GrantInput(
                grantee_type="account",
                grantee_id=account.user_id,
                resource_type="version",
                resource_id="version_a",
                allowed_actions=["version:edit"],
                environments=["test"],
            ),
        )

    results = await asyncio.gather(*(grant(i) for i in range(6)), return_exceptions=True)
    assert sum(not isinstance(r, Exception) for r in results) == 1
    assert all(r.status == 409 for r in results if isinstance(r, ServiceError))


async def test_scope_escalation_and_foreign_resources_are_rejected(iam_env, admin, manager):
    iam = iam_env[0]
    for action in ("release:publish", "data:read_sensitive", "data:export"):
        with pytest.raises(ServiceError) as exc:
            await iam.access.put_grant(
                manager[1],
                "channel_a",
                "elevate",
                GrantInput(
                    grantee_type="account",
                    grantee_id=manager[1].account.id,
                    resource_type="version",
                    resource_id="version_a",
                    allowed_actions=[action],
                    environments=["prod"],
                ),
            )
        assert exc.value.status == 403
    with pytest.raises(ServiceError) as exc:
        await iam.access.put_grant(
            manager[1],
            "channel_a",
            "foreign_grant",
            GrantInput(
                grantee_type="account",
                grantee_id=manager[1].account.id,
                resource_type="version",
                resource_id="foreign",
                allowed_actions=["version:read"],
                environments=["test"],
            ),
        )
    assert exc.value.status == 404


async def test_latent_role_grant_cannot_be_activated_by_member_edit(iam_env, admin, manager):
    iam, _, _, engine, _ = iam_env
    account, _ = await create_user(iam, admin[1])
    # 夹具模拟另一个合法授权人以前给 builder 角色授予过独立发布权限。
    grant_id = new_id("grant")
    scope = Scope(channel_id="channel_a", environment="prod")
    async with transaction(
        engine,
        scope,
        [policy_key("channel_a"), record_key("channel_a", "resource_grants", grant_id)],
    ) as uow:
        await save(
            uow,
            "resource_grants",
            grant_id,
            dict(
                grantee_type="role",
                grantee_id="builder",
                resource_type="version",
                resource_id="version_a",
                allowed_actions=["release:publish"],
                environments=["prod"],
            ),
        )
    with pytest.raises(ServiceError) as exc:
        await iam.access.put_member(
            manager[1],
            "channel_a",
            account.user_id,
            MembershipInput(roles=["builder"], environments=["prod"]),
        )
    assert exc.value.status == 403
    async with engine.connect() as connection:
        assert (
            await one(connection, "channel_memberships", "channel_a", user_id=account.user_id)
            is None
        )


async def test_audit_filters_actual_changed_scope_and_records_denied_result(
    iam_env, admin, manager
):
    iam = iam_env[0]
    await iam.access.put_grant(
        manager[1],
        "channel_a",
        "production_grant",
        GrantInput(
            grantee_type="account",
            grantee_id=admin[1].account.id,
            resource_type="version",
            resource_id="version_a",
            allowed_actions=["version:read"],
            environments=["prod"],
        ),
    )
    assert "production_grant" not in {
        event.target_id for event in await iam.audit.query(manager[1])
    }
    _, temporary = await login(iam, "root-admin")
    _, production = await enter(iam, temporary, environment="prod")
    assert "production_grant" in {event.target_id for event in await iam.audit.query(production)}
    with pytest.raises(ServiceError):
        await iam.access.put_grant(
            manager[1],
            "channel_a",
            "denied_grant",
            GrantInput(
                grantee_type="account",
                grantee_id=admin[1].account.id,
                resource_type="version",
                resource_id="version_a",
                allowed_actions=["data:read_sensitive"],
                environments=["test"],
            ),
        )
    assert any(
        event.outcome == "DENIED" and event.target_id == "denied_grant"
        for event in await iam.audit.query(manager[1])
    )


async def test_member_revocation_during_password_hash_is_rechecked_in_transaction(
    iam_env, admin, manager, monkeypatch
):
    iam = iam_env[0]
    account, (_, session) = await builder(iam, admin[1], manager[1])
    before = await iam.accounts.repository.account(account.user_id)
    original_hash = iam.accounts.passwords.hash

    async def revoke_then_hash(password):
        member = await iam.accounts.repository.membership("channel_a", account.user_id)
        await iam.access.remove_member(manager[1], "channel_a", account.user_id, member.revision)
        return await original_hash(password)

    monkeypatch.setattr(iam.accounts.passwords, "hash", revoke_then_hash)
    with pytest.raises(ServiceError) as exc:
        await iam.accounts.change_password(
            session, PasswordChange(current_password=PASSWORD, new_password="Another-password-4455")
        )
    assert exc.value.code == "MEMBERSHIP_DISABLED"
    assert (
        await iam.accounts.repository.account(account.user_id)
    ).credential_version == before.credential_version
