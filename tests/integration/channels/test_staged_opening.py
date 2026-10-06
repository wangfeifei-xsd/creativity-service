"""渠道可分步开通，环境创建与管理员授权、恢复屏障同事务提交。"""

import asyncio

import pytest
from sqlalchemy import select

from creativity_service.core.context import Scope
from creativity_service.core.deletion import barrier_id
from creativity_service.core.primitives import ServiceError
from creativity_service.modules.channels.schemas import ChannelCreate, EnvironmentCreate
from creativity_service.modules.iam.schemas import ChannelContextInput
from creativity_service.storage import metadata

pytestmark = pytest.mark.integration


async def staged_channel(env):
    return await env.services.channels.create(
        env.admin,
        ChannelCreate(
            name="分步开通渠道",
            owner="负责人",
            first_admin_user_id=env.user_id,
            independent_actions=["release:publish"],
        ),
    )


async def test_staged_channel_environment_activates_authorization(channel_env):
    env = channel_env
    channel = await staged_channel(env)
    cid = channel.channel_id
    assert await env.iam.sessions.channels(env.admin) == []
    with pytest.raises(ServiceError):
        await env.iam.sessions.enter(
            env.admin, ChannelContextInput(channel_id=cid, environment="test")
        )
    await env.services.channels.create_environment(
        env.admin, cid, EnvironmentCreate(environment="test", name="测试")
    )
    options = await env.iam.sessions.channels(env.admin)
    assert [(o.channel_id, o.environment) for o in options] == [(cid, "test")]
    assert set(options[0].model_dump()) == {
        "channel_id",
        "channel_name",
        "environment",
        "environment_name",
    }
    token = await env.iam.sessions.enter(
        env.admin, ChannelContextInput(channel_id=cid, environment="test")
    )
    manager = await env.iam.authentication.admin_session(token.access_token, "staged")
    for action in ("model:manage", "run:create", "release:publish"):
        await env.iam.authorization.require(manager.context, action, "new")
    barrier = metadata.tables["recovery_barriers"]
    async with env.engine.connect() as db:
        row = (
            (await db.execute(select(barrier).where(barrier.c.channel_id == cid))).mappings().one()
        )
    assert row["id"] == barrier_id(Scope(channel_id=cid, environment="test"))
    assert row["state"] == "READY"


async def test_environment_authorization_failure_rolls_back(channel_env, monkeypatch):
    import creativity_service.modules.channels.services as module

    env = channel_env
    channel = await staged_channel(env)

    async def failure(*args, **kwargs):
        raise RuntimeError("授权失败")

    monkeypatch.setattr(module, "extend_environment_assignments", failure)
    with pytest.raises(RuntimeError, match="授权失败"):
        await env.services.channels.create_environment(
            env.admin, channel.channel_id, EnvironmentCreate(environment="dev", name="开发")
        )
    assert await env.services.channels.environments(env.admin, channel.channel_id) == []
    assert await env.iam.sessions.channels(env.admin) == []


async def test_concurrent_environment_creation_keeps_one_entry(channel_env):
    env = channel_env
    channel = await staged_channel(env)
    results = await asyncio.gather(
        *[
            env.services.channels.create_environment(
                env.admin, channel.channel_id, EnvironmentCreate(environment="dev", name="开发")
            )
            for _ in range(4)
        ],
        return_exceptions=True,
    )
    assert sum(not isinstance(result, Exception) for result in results) == 1
    assert len(await env.iam.sessions.channels(env.admin)) == 1
