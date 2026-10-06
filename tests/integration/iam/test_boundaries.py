"""交接入口、独立动作、事务回滚与流/文件边界复核。"""

import pytest

from creativity_service.core.artifacts import ArtifactService
from creativity_service.core.auth.types import Revocation
from creativity_service.core.context import Scope
from creativity_service.core.database import transaction
from creativity_service.core.deletion import ContentRef, RecoveryService
from creativity_service.core.primitives import ServiceError, new_id, utcnow
from creativity_service.modules.iam.repositories import rows
from creativity_service.modules.iam.schemas import (
    ChannelContextInput,
    GrantInput,
    MembershipInput,
    PasswordReset,
)

from .conftest import create_user, enter, login, provision
from .test_authorization import builder

pytestmark = pytest.mark.integration


async def test_default_missing_channel_adapter_cannot_open_workspace(iam_env, admin):
    iam, client, _, _, _ = iam_env
    iam.sessions.directory = None
    headers = {"Authorization": f"Bearer {admin[0].access_token}"}
    assert (await client.get("/admin/v1/auth/session", headers=headers)).status_code == 200
    assert (await client.get("/admin/v1/auth/channels", headers=headers)).status_code == 503
    result = await client.post(
        "/admin/v1/auth/channel-context",
        headers=headers,
        json={"channel_id": "channel_a", "environment": "test", "data_scope_id": "domain_a"},
    )
    assert result.status_code == 503


async def test_reset_racing_workspace_switch_cannot_refresh_old_identity(iam_env, admin, manager):
    iam = iam_env[0]
    target = manager[1]
    row = await iam.accounts.repository.account(target.account.id)
    await iam.accounts.reset(
        admin[1],
        target.account.id,
        PasswordReset(revision=row.revision, initial_password="New-reset-secret-1111"),
    )
    with pytest.raises(ServiceError) as exc:
        await iam.sessions.enter(
            target,
            ChannelContextInput(
                channel_id="channel_a", environment="test", data_scope_id="domain_a"
            ),
        )
    assert exc.value.status == 401


async def test_first_member_provisioning_rolls_back_with_channel_transaction(iam_env, admin):
    iam, _, _, engine, _ = iam_env
    scope = Scope(channel_id="channel_a", environment="test", data_scope_id="domain_a")
    with pytest.raises(RuntimeError, match="开通失败"):
        async with transaction(
            engine, scope, iam.access.provisioning_keys("channel_a", admin[1].account.id)
        ) as uow:
            await iam.access.provision_first_member(
                uow, admin[1], admin[1].account.id, ["test"], ["domain_a"]
            )
            raise RuntimeError("开通失败")
    async with engine.connect() as connection:
        for table in ("channel_memberships", "resource_grants", "audit_events"):
            assert await rows(connection, table, "channel_a") == []


async def test_independent_publish_requires_explicit_resource_and_scope(iam_env, admin):
    iam = iam_env[0]
    await provision(iam, admin[1], "channel_a", admin[1].account.id, ["release:publish"])
    _, platform_login = await login(iam, "root-admin")
    _, manager = await enter(iam, platform_login)
    account, (_, temporary) = await create_user(iam, admin[1])
    await iam.access.put_member(
        manager,
        "channel_a",
        account.user_id,
        MembershipInput(roles=["builder"], environments=["test", "prod"], data_scopes=["domain_a"]),
    )
    await iam.access.put_grant(
        manager,
        "channel_a",
        "publish_permission",
        GrantInput(
            grantee_type="account",
            grantee_id=account.user_id,
            resource_type="version",
            resource_id="version_a",
            allowed_actions=["release:publish"],
            environments=["prod"],
            data_scopes=["domain_a"],
        ),
    )
    _, subject = await enter(iam, temporary, environment="prod")
    await iam.authorization.require(subject.context, "release:publish", "version_a")
    with pytest.raises(ServiceError) as exc:
        await iam.authorization.require(subject.context, "release:publish", "version_b")
    assert exc.value.status == 403
    with pytest.raises(ServiceError):
        await iam.authorization.boundary(subject.context, "data:export", "version", "version_a")


async def test_file_bytes_are_not_delivered_after_token_revocation(iam_env, admin):
    iam, _, _, engine, _ = iam_env
    await provision(
        iam, admin[1], "channel_a", admin[1].account.id, ["data:export", "data:read_sensitive"]
    )
    _, temporary = await login(iam, "root-admin")
    _, manager = await enter(iam, temporary)
    context = manager.context
    await RecoveryService(engine, iam.authorization).initialize_fresh(context)

    class Storage:
        def __init__(self):
            self.content = None
            self.revoke_on_read = False

        async def put(self, key, data, content_type):
            self.content = data

        async def get(self, key, max_bytes):
            if self.revoke_on_read:
                await iam.authentication.tokens.revoke(
                    Revocation(
                        id=new_id("revoke"),
                        channel_id="channel_a",
                        kind="token",
                        target_id=context.token_digest,
                        cutoff_at=utcnow(),
                    )
                )
            return self.content

        async def delete(self, key):
            self.content = None

    storage = Storage()
    artifacts = ArtifactService(engine, storage, iam.authorization)
    artifact = await artifacts.upload(
        context, "报告.txt", "text/plain", b"controlled content", [ContentRef("input", "input_a")]
    )
    assert (await artifacts.download(context, artifact.artifact_id))[0] == b"controlled content"
    storage.revoke_on_read = True
    with pytest.raises(ServiceError) as exc:
        await artifacts.download(context, artifact.artifact_id)
    assert exc.value.status == 401


async def test_background_source_survives_browser_logout_but_not_membership_revocation(
    iam_env, admin, manager
):
    iam = iam_env[0]
    # 使用渠道可分配的普通成员验证撤销，渠道管理员只能由平台调整。
    account, (_, session) = await builder(iam, admin[1], manager[1])
    source = await iam.authentication.identity_source(session.context)
    assert "token_digest" not in source.model_dump() and "session_id" not in source.model_dump()
    await iam.sessions.logout(session)
    worker = source.worker_context(new_id("request"))
    await iam.authorization.boundary(worker, "version:edit", "version", "version_a")
    member = await iam.accounts.repository.membership("channel_a", account.user_id)
    await iam.access.remove_member(manager[1], "channel_a", account.user_id, member.revision)
    with pytest.raises(ServiceError) as exc:
        await iam.authorization.boundary(worker, "version:edit", "version", "version_a")
    assert exc.value.status == 401
