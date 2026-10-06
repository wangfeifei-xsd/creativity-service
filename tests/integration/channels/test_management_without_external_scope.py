"""外部数据域尚未接入时，管理工作区仍须有可验证的独立权限范围。"""

import pytest

from creativity_service.core.primitives import ServiceError, new_id
from creativity_service.modules.channels.presentation import page_view
from creativity_service.modules.channels.repositories import management_scope_id
from creativity_service.modules.channels.schemas import (
    ChannelCreate,
    DataScopeCreate,
    EnvironmentCreate,
)
from creativity_service.modules.iam.schemas import ChannelContextInput

from .conftest import login

pytestmark = pytest.mark.integration


async def test_channel_management_does_not_require_external_mapping(channel_env):
    env = channel_env
    created = await env.services.channels.create(
        env.admin,
        ChannelCreate(name="仅平台管理渠道", owner="负责人", first_admin_user_id=env.user_id),
    )
    channel_id = created.channel_id
    await env.services.channels.create_environment(
        env.admin, channel_id, EnvironmentCreate(environment="test", name="测试")
    )
    assert await env.services.channels.data_scopes(env.admin, channel_id) == []
    _, session = await login(env)
    options = await env.iam.sessions.channels(session)
    manage_id = management_scope_id(channel_id, "test")
    assert [(o.channel_id, o.data_scope_id) for o in options] == [(channel_id, manage_id)]
    token = await env.iam.sessions.enter(
        session,
        ChannelContextInput(channel_id=channel_id, environment="test", data_scope_id=manage_id),
    )
    manager = await env.iam.authentication.admin_session(
        token.access_token, new_id("request"), governance=True
    )
    assert (await env.services.channels.list_page(manager)).items[0].channel_id == channel_id
    assert (await env.services.channels.environments(manager, channel_id))[0].name == "测试"
    domain = await env.services.channels.create_data_scope(
        manager,
        channel_id,
        DataScopeCreate(
            name="俱乐部甲",
            environment="test",
            external_scope_type="club",
            external_scope_id="club-001",
        ),
    )
    _, session = await login(env)
    refreshed = await env.iam.sessions.enter(
        session,
        ChannelContextInput(channel_id=channel_id, environment="test", data_scope_id=manage_id),
    )
    manager = await env.iam.authentication.admin_session(
        refreshed.access_token, new_id("request"), governance=True
    )
    assert [
        item.data_scope_id for item in await env.services.channels.data_scopes(manager, channel_id)
    ] == [domain.data_scope_id]
    with pytest.raises(ServiceError) as denied:
        await env.iam.authorization.require(manager.context, "run:create", "new")
    assert denied.value.code == "FORBIDDEN"


async def test_existing_environment_can_explicitly_enable_management(channel_env):
    env = channel_env
    created = await env.services.channels.create(
        env.admin,
        ChannelCreate(name="旧渠道管理入口", owner="负责人", first_admin_user_id=env.user_id),
    )
    channel_id = created.channel_id
    await env.services.channels.create_environment(
        env.admin, channel_id, EnvironmentCreate(environment="test", name="测试")
    )
    # 模拟改造前已有环境、首位管理员仅登记身份而没有管理范围。
    member = await env.iam.access.repository.membership(channel_id, env.user_id)
    initial = next(
        grant
        for grant in await env.iam.access.repository.grants(channel_id)
        if grant.id == "initial_" + member.id
    )
    from sqlalchemy import update

    from creativity_service.modules.iam.repositories import TABLES

    async with env.engine.begin() as connection:
        for name, row in (("channel_memberships", member), ("resource_grants", initial)):
            table = TABLES[name]
            await connection.execute(
                update(table)
                .where(table.c.channel_id == channel_id, table.c.id == row.id)
                .values(environments=[], data_scopes=[])
            )
        grant = next(
            grant
            for grant in await env.iam.access.repository.grants(channel_id)
            if grant.id.startswith("workspace_")
        )
        await connection.execute(
            TABLES["resource_grants"]
            .delete()
            .where(
                TABLES["resource_grants"].c.channel_id == channel_id,
                TABLES["resource_grants"].c.id == grant.id,
            )
        )
        await connection.execute(
            TABLES["audit_events"]
            .delete()
            .where(
                TABLES["audit_events"].c.channel_id == channel_id,
                TABLES["audit_events"].c.id == grant.id,
            )
        )
    assert await env.iam.sessions.channels(env.admin) == []
    assert (
        await page_view(env.services.channels, env.admin, channel_id)
    ).management_missing_environments == ["test"]
    await env.services.channels.enable_management_workspace(env.admin, channel_id, "test")
    await env.services.channels.enable_management_workspace(env.admin, channel_id, "test")
    assert (
        await page_view(env.services.channels, env.admin, channel_id)
    ).management_missing_environments == []
    assert [item.data_scope_id for item in await env.iam.sessions.channels(env.admin)] == [
        management_scope_id(channel_id, "test")
    ]


async def test_second_environment_gets_management_access_after_first_real_scope(channel_env):
    env = channel_env
    channel = await env.services.channels.create(
        env.admin,
        ChannelCreate(name="多环境目录渠道", owner="负责人", first_admin_user_id=env.user_id),
    )
    channel_id = channel.channel_id
    await env.services.channels.create_environment(
        env.admin, channel_id, EnvironmentCreate(environment="test", name="测试")
    )
    await env.services.channels.create_data_scope(
        env.admin,
        channel_id,
        DataScopeCreate(
            name="俱乐部甲",
            environment="test",
            external_scope_type="club",
            external_scope_id="club-001",
            administrator_id=env.user_id,
        ),
    )
    _, session = await login(env)
    manage_token = await env.iam.sessions.enter(
        session,
        ChannelContextInput(
            channel_id=channel_id,
            environment="test",
            data_scope_id=management_scope_id(channel_id, "test"),
        ),
    )
    current_manager = await env.iam.authentication.admin_session(
        manage_token.access_token, new_id("request"), governance=True
    )
    await env.services.channels.create_environment(
        current_manager, channel_id, EnvironmentCreate(environment="prod", name="生产")
    )
    _, session = await login(env)
    options = await env.iam.sessions.channels(session)
    assert management_scope_id(channel_id, "prod") in {item.data_scope_id for item in options}
    token = await env.iam.sessions.enter(
        session,
        ChannelContextInput(
            channel_id=channel_id,
            environment="prod",
            data_scope_id=management_scope_id(channel_id, "prod"),
        ),
    )
    manager = await env.iam.authentication.admin_session(
        token.access_token, new_id("request"), governance=True
    )
    assert {
        item.name for item in await env.services.channels.environments(manager, channel_id)
    } == {"生产"}
