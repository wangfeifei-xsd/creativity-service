"""两种管理身份、多渠道授权、默认渠道及撤销的真实集成验证。"""

import asyncio

import pytest

from creativity_service.core.primitives import ServiceError, new_id
from creativity_service.modules.channels.schemas import (
    ChannelCreate,
    EnvironmentCreate,
    EnvironmentUpdate,
)
from creativity_service.modules.iam.repositories import rows
from creativity_service.modules.iam.schemas import (
    AccountCreate,
    AccountUpdate,
    ChannelContextInput,
    LoginInput,
    MembershipInput,
    PasswordChange,
)
from tests.support.captcha import captcha_token

from .conftest import INITIAL, PASSWORD, provision

pytestmark = pytest.mark.integration


async def login_user(env, account, *, password=PASSWORD, initial=False):
    result = await env.iam.sessions.login(
        LoginInput(
            login_name=account.login_name,
            password=password,
            captcha_token=await captcha_token(env.iam, account.login_name, "admin-accounts"),
        ),
        "admin-accounts",
        new_id("request"),
    )
    session = await env.iam.authentication.admin_session(
        result.access_token,
        new_id("request"),
        allow_initial=initial,
    )
    return result, session


async def activate(env, account):
    _, initial = await login_user(env, account, password=INITIAL, initial=True)
    await env.iam.accounts.change_password(
        initial,
        PasswordChange(current_password=INITIAL, new_password=PASSWORD),
    )
    return await login_user(env, account)


@pytest.mark.parametrize("assign_before_first_environment", [False, True])
async def test_new_environment_reaches_all_assigned_administrators(
    channel_env, assign_before_first_environment
):
    env = channel_env
    channel = await env.services.channels.create(
        env.admin,
        ChannelCreate(name="环境同步渠道", owner="负责人", first_admin_user_id=env.user_id),
    )
    channel_id = channel.channel_id
    body = AccountCreate(
        login_name="environment-admin",
        display_name="后分配管理员",
        initial_password=INITIAL,
        role="channel_admin",
        channel_ids=[channel_id],
    )
    account = (
        await env.iam.accounts.create(env.admin, body) if assign_before_first_environment else None
    )
    await env.services.channels.create_environment(
        env.admin, channel_id, EnvironmentCreate(environment="dev", name="开发环境")
    )
    account = account or await env.iam.accounts.create(env.admin, body)
    _, session = await activate(env, account)
    assert {option.environment for option in await env.iam.sessions.channels(session)} == {"dev"}
    await env.services.channels.create_environment(
        env.admin, channel_id, EnvironmentCreate(environment="prod", name="生产环境")
    )
    # 原平台管理会话读取最新成员目录；无需重新保存账号的渠道授权。
    options = await env.iam.sessions.channels(session)
    assert {option.environment for option in options} == {"dev", "prod"}
    token = await env.iam.sessions.enter(
        session,
        ChannelContextInput(
            channel_id=channel_id,
            environment="dev",
        ),
    )
    headers = {"Authorization": "Bearer " + token.access_token}
    path = f"/admin/v1/channels/{channel_id}/environments"
    response = await env.client.get(path, headers=headers)
    assert response.status_code == 200
    assert {item["name"] for item in response.json()} == {"开发环境", "生产环境"}
    prod = next(item for item in response.json() if item["environment"] == "prod")
    edited = await env.client.patch(
        path + "/prod", headers=headers, json={"revision": prod["revision"], "name": "正式环境"}
    )
    assert edited.status_code == 200 and edited.json()["name"] == "正式环境"
    grants_before = await env.iam.access.repository.grants(channel_id)
    # 修复入口重复调用不增加修订，也不新授予发布、导出等独立动作。
    assert await env.iam.access.repository.grants(channel_id) == grants_before


async def test_new_environment_preserves_restricted_and_disabled_assignments(channel_env):
    from sqlalchemy import update

    from creativity_service.modules.iam.account_channels import administrator_grant_id
    from creativity_service.modules.iam.repositories import TABLES

    env = channel_env
    channel = await env.services.channels.create(
        env.admin,
        ChannelCreate(name="环境授权边界", owner="负责人", first_admin_user_id=env.user_id),
    )
    channel_id = channel.channel_id
    await env.services.channels.create_environment(
        env.admin, channel_id, EnvironmentCreate(environment="dev", name="开发")
    )
    accounts = []
    for name in ("restricted", "disabled"):
        accounts.append(
            await env.iam.accounts.create(
                env.admin,
                AccountCreate(
                    login_name=name,
                    display_name=name,
                    initial_password=INITIAL,
                    role="channel_admin",
                    channel_ids=[channel_id],
                ),
            )
        )
    await env.iam.accounts.update(
        env.admin,
        accounts[1].user_id,
        AccountUpdate(revision=accounts[1].revision, status="DISABLED"),
    )
    grants = TABLES["resource_grants"]
    async with env.engine.begin() as connection:
        await connection.execute(
            update(grants)
            .where(
                grants.c.channel_id == channel_id,
                grants.c.id == administrator_grant_id(channel_id, accounts[0].user_id),
            )
            .values(allowed_actions=["environment:manage"])
        )
    await env.services.channels.create_environment(
        env.admin, channel_id, EnvironmentCreate(environment="prod", name="生产")
    )
    for account in accounts:
        member = await env.iam.access.repository.membership(channel_id, account.user_id)
        assert member.environments == ["dev"]


async def test_two_account_roles_multichannel_default_and_revocation(channel_env):
    env = channel_env
    a = await provision(env, "alpha", None)
    b = await provision(env, "beta", None)
    foreign = await provision(env, "foreign", None)
    assert [r.role_code for r in await env.iam.accounts.role_options(env.admin)] == [
        "platform_admin",
        "channel_admin",
    ]
    platform = await env.iam.sessions.view(env.admin)
    assert platform.can_access_platform and platform.default_workspace is None
    account = await env.iam.accounts.create(
        env.admin,
        AccountCreate(
            login_name="channel-manager",
            display_name="渠道管理员",
            initial_password=INITIAL,
            role="channel_admin",
            channel_ids=[b.channel.channel_id, a.channel.channel_id],
        ),
    )
    assert account.role_name == "渠道管理员" and account.platform_roles == []
    assert set(account.channel_ids) == {a.channel.channel_id, b.channel.channel_id}
    assert set(account.channel_names) == {a.channel.name, b.channel.name}
    _, session = await activate(env, account)
    view = await env.iam.sessions.view(session)
    assert not view.can_access_platform
    assert view.default_workspace.channel_id == a.channel.channel_id
    assert {o.channel_id for o in view.workspace_options} == set(account.channel_ids)
    first = await env.iam.sessions.enter(
        session,
        ChannelContextInput(
            **{key: getattr(view.default_workspace, key) for key in ("channel_id", "environment")}
        ),
    )
    manager = await env.iam.authentication.admin_session(first.access_token, new_id("request"))
    active = await env.iam.sessions.view(manager)
    assert active.workspace.channel_id == a.channel.channel_id
    assert active.default_workspace is None and not active.can_access_platform
    with pytest.raises(ServiceError) as no_platform:
        await env.iam.sessions.enter_platform(manager)
    assert no_platform.value.status == 403
    with pytest.raises(ServiceError) as denied:
        await env.iam.sessions.enter(
            manager,
            ChannelContextInput(
                channel_id=foreign.channel.channel_id,
                environment="test",
            ),
        )
    assert denied.value.status == 404
    auth = {"Authorization": "Bearer " + first.access_token}
    assert (await env.client.get("/admin/v1/accounts/roles", headers=auth)).status_code == 403
    assert (await env.client.get("/admin/v1/accounts/page", headers=auth)).status_code == 403
    current = await env.iam.accounts.get(env.admin, account.user_id)
    updated = await env.iam.accounts.update(
        env.admin,
        account.user_id,
        AccountUpdate(
            revision=current.revision,
            role="channel_admin",
            channel_ids=[b.channel.channel_id],
        ),
    )
    assert updated.channel_ids == [b.channel.channel_id]
    assert (await env.client.get("/admin/v1/auth/session", headers=auth)).status_code == 401
    _, fresh = await login_user(env, account)
    fresh_view = await env.iam.sessions.view(fresh)
    assert fresh_view.default_workspace.channel_id == b.channel.channel_id
    assert {o.channel_id for o in fresh_view.workspace_options} == {b.channel.channel_id}
    async with env.engine.connect() as connection:
        members = await rows(
            connection,
            "channel_memberships",
            a.channel.channel_id,
            user_id=account.user_id,
        )
    assert members[0]["status"] == "DISABLED"


async def test_multichannel_validation_and_revision_conflict_roll_back_all_channels(channel_env):
    env = channel_env
    a = await provision(env, "alpha", None)
    b = await provision(env, "beta", None)
    with pytest.raises(ServiceError) as invalid:
        await env.iam.accounts.create(
            env.admin,
            AccountCreate(
                login_name="invalid-admin",
                display_name="无效管理员",
                initial_password=INITIAL,
                role="channel_admin",
                channel_ids=[a.channel.channel_id, "missing-channel"],
            ),
        )
    assert invalid.value.status == 422
    async with env.engine.connect() as connection:
        assert (
            await rows(
                connection,
                "platform_accounts",
                "system",
                login_name="invalid-admin",
            )
            == []
        )
    account = await env.iam.accounts.create(
        env.admin,
        AccountCreate(
            login_name="atomic-admin",
            display_name="原名称",
            initial_password=INITIAL,
            role="channel_admin",
            channel_ids=[a.channel.channel_id],
        ),
    )
    current = await env.iam.accounts.update(
        env.admin,
        account.user_id,
        AccountUpdate(revision=account.revision, display_name="最新名称"),
    )
    with pytest.raises(ServiceError) as conflict:
        await env.iam.accounts.update(
            env.admin,
            account.user_id,
            AccountUpdate(
                revision=account.revision,
                role="channel_admin",
                channel_ids=[b.channel.channel_id],
            ),
        )
    assert conflict.value.status == 409
    loaded = await env.iam.accounts.get(env.admin, account.user_id)
    assert loaded.revision == current.revision
    assert loaded.display_name == "最新名称" and loaded.channel_ids == [a.channel.channel_id]
    async with env.engine.connect() as connection:
        assert (
            await rows(
                connection,
                "channel_memberships",
                b.channel.channel_id,
                user_id=account.user_id,
            )
            == []
        )
        grants = await rows(
            connection,
            "resource_grants",
            b.channel.channel_id,
            grantee_id=account.user_id,
        )
        assert grants == []
    headers = {"Authorization": "Bearer " + env.admin_token.access_token}
    for role, selected in (
        ("channel_admin", [a.channel.channel_id, a.channel.channel_id]),
        ("channel_admin", ["system"]),
        ("platform_admin", [a.channel.channel_id]),
        ("builder", []),
    ):
        response = await env.client.post(
            "/admin/v1/accounts",
            headers=headers,
            json={
                "login_name": "rejected-role",
                "display_name": "无效角色",
                "initial_password": INITIAL,
                "role": role,
                "channel_ids": selected,
            },
        )
        assert response.status_code == 422


async def test_concurrent_channel_assignments_and_role_conversion(channel_env):
    env = channel_env
    a = await provision(env, "alpha", None)
    b = await provision(env, "beta", None)
    account = await env.iam.accounts.create(
        env.admin,
        AccountCreate(
            login_name="concurrent-admin",
            display_name="并发管理员",
            initial_password=INITIAL,
            role="channel_admin",
            channel_ids=[a.channel.channel_id],
        ),
    )
    outcomes = await asyncio.wait_for(
        asyncio.gather(
            *(
                env.iam.accounts.update(
                    env.admin,
                    account.user_id,
                    AccountUpdate(
                        revision=account.revision,
                        role="channel_admin",
                        channel_ids=ids,
                    ),
                )
                for ids in ([a.channel.channel_id, b.channel.channel_id], [b.channel.channel_id])
            ),
            return_exceptions=True,
        ),
        timeout=15,
    )
    assert sum(not isinstance(result, Exception) for result in outcomes) == 1
    assert (
        sum(isinstance(result, ServiceError) and result.status == 409 for result in outcomes) == 1
    )
    current = await env.iam.accounts.get(env.admin, account.user_id)
    converted = await env.iam.accounts.update(
        env.admin,
        account.user_id,
        AccountUpdate(
            revision=current.revision,
            role="platform_admin",
            channel_ids=[],
        ),
    )
    assert converted.role == "platform_admin" and converted.channel_ids == []
    assert converted.platform_roles == ["platform_admin"]
    _, session = await activate(env, converted)
    view = await env.iam.sessions.view(session)
    assert view.can_access_platform and view.default_workspace is None
    assert view.workspace_options == []


async def test_pending_channel_assignment_never_creates_unscoped_access(channel_env):
    env = channel_env
    channel = await env.services.channels.create(
        env.admin,
        ChannelCreate(
            name="待配置渠道",
            owner="负责人",
            first_admin_user_id=env.user_id,
        ),
    )
    account = await env.iam.accounts.create(
        env.admin,
        AccountCreate(
            login_name="pending-channel-admin",
            display_name="待配置管理员",
            initial_password=INITIAL,
            role="channel_admin",
            channel_ids=[channel.channel_id],
        ),
    )
    assert account.channel_ids == [channel.channel_id]
    _, session = await activate(env, account)
    view = await env.iam.sessions.view(session)
    assert view.workspace is None and view.default_workspace is None
    assert view.workspace_options == [] and not view.can_access_platform
    async with env.engine.connect() as connection:
        member = (
            await rows(
                connection,
                "channel_memberships",
                channel.channel_id,
                user_id=account.user_id,
            )
        )[0]
        grant = (
            await rows(
                connection,
                "resource_grants",
                channel.channel_id,
                grantee_id=account.user_id,
            )
        )[0]
    assert member["environments"] == []
    assert grant["environments"] == []


@pytest.mark.parametrize("pending_assignment", [False, True])
async def test_account_assignment_grants_management_without_external_scopes(
    channel_env, pending_assignment
):
    env = channel_env
    channel = await env.services.channels.create(
        env.admin,
        ChannelCreate(name="尚未接入业务渠道", owner="负责人", first_admin_user_id=env.user_id),
    )
    account = None
    body = AccountCreate(
        login_name="management-only-admin",
        display_name="渠道管理员",
        initial_password=INITIAL,
        role="channel_admin",
        channel_ids=[channel.channel_id],
    )
    if pending_assignment:
        account = await env.iam.accounts.create(env.admin, body)
    await env.services.channels.create_environment(
        env.admin, channel.channel_id, EnvironmentCreate(environment="test", name="测试")
    )
    disabled = await env.services.channels.create_environment(
        env.admin, channel.channel_id, EnvironmentCreate(environment="prod", name="生产")
    )
    await env.services.channels.update_environment(
        env.admin,
        channel.channel_id,
        "prod",
        EnvironmentUpdate(revision=disabled.revision, status="DISABLED"),
    )
    if account:
        # 已有空范围账号重新保存授权时也必须修复，不能只覆盖新建账号。
        account = await env.iam.accounts.update(
            env.admin,
            account.user_id,
            AccountUpdate(
                revision=account.revision, role="channel_admin", channel_ids=[channel.channel_id]
            ),
        )
    else:
        account = await env.iam.accounts.create(env.admin, body)
    _, session = await activate(env, account)
    view = await env.iam.sessions.view(session)
    assert not view.can_access_platform
    assert len(view.workspace_options) == 1
    workspace = view.default_workspace
    assert workspace is not None and workspace.channel_id == channel.channel_id
    assert workspace.environment == "test"
    token = await env.iam.sessions.enter(
        session,
        ChannelContextInput(**workspace.model_dump(include={"channel_id", "environment"})),
    )
    headers = {"Authorization": "Bearer " + token.access_token}
    response = await env.client.get("/admin/v1/channels/page", headers=headers)
    assert response.status_code == 200
    assert [item["channel_id"] for item in response.json()["items"]] == [channel.channel_id]
    prefix = f"/admin/v1/channels/{channel.channel_id}"
    assert (await env.client.get(prefix + "/data-scopes", headers=headers)).status_code == 404
    assert (await env.client.get(prefix + "/environments", headers=headers)).status_code == 200
    assert (await env.client.get("/admin/v1/accounts/page", headers=headers)).status_code == 403
    manager = await env.iam.authentication.admin_session(token.access_token, new_id("request"))
    await env.iam.authorization.require(manager.context, "run:create", "new")


async def test_channel_multiselect_does_not_leave_hidden_legacy_memberships(channel_env):
    env = channel_env
    a = await provision(env, "alpha", None)
    b = await provision(env, "beta", None)
    account = await env.iam.accounts.create(
        env.admin,
        AccountCreate(
            login_name="legacy-scoped-admin",
            display_name="历史授权管理员",
            initial_password=INITIAL,
            role="channel_admin",
            channel_ids=[a.channel.channel_id],
        ),
    )
    await env.iam.access.put_member(
        b.manager,
        b.channel.channel_id,
        account.user_id,
        MembershipInput(
            roles=["auditor"],
            environments=["test"],
        ),
    )
    current = await env.iam.accounts.get(env.admin, account.user_id)
    assert set(current.channel_ids) == {a.channel.channel_id, b.channel.channel_id}
    await env.iam.accounts.update(
        env.admin,
        account.user_id,
        AccountUpdate(
            revision=current.revision,
            role="channel_admin",
            channel_ids=[a.channel.channel_id],
        ),
    )
    async with env.engine.connect() as connection:
        member = (
            await rows(
                connection,
                "channel_memberships",
                b.channel.channel_id,
                user_id=account.user_id,
            )
        )[0]
    assert member["status"] == "DISABLED"
