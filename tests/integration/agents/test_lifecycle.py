"""单份配置、独立环境状态与历史数据迁移的真实 MySQL 回归。"""

from datetime import timedelta

import pytest
from alembic.config import Config
from sqlalchemy import inspect, select, update

from alembic import command
from creativity_service.core.primitives import ServiceError, digest
from creativity_service.modules.agents.lifecycle import AgentLifecycle
from creativity_service.modules.agents.repositories import repository
from creativity_service.modules.agents.schemas import AgentConfigurationEdit, AgentStateInput

pytestmark = pytest.mark.integration


async def test_online_and_enabled_are_independent_and_edits_keep_one_configuration(agent_env):
    env = agent_env
    detail = await env.agents.create(env.context, env.body)
    aid = detail.agent.agent_id
    lifecycle = AgentLifecycle(env.agents)
    assert not detail.agent.online and detail.agent.enabled
    original = await env.agents.freeze_candidate(env.context, aid, 1)
    for operation, online, enabled in [
        ("online", True, True),
        ("disable", True, False),
        ("offline", False, False),
        ("enable", False, True),
        ("online", True, True),
    ]:
        detail = await lifecycle.state(
            env.context,
            aid,
            AgentStateInput(
                revision=detail.agent.revision, operation=operation, reason="验证独立状态"
            ),
        )
        assert (detail.agent.online, detail.agent.enabled) == (online, enabled)
        if not online or not enabled:
            with pytest.raises(ServiceError):
                await env.agents.resolve_current(env.context, env.body.agent_code)
        if not enabled:
            with pytest.raises(ServiceError, match="已停用"):
                await env.agents.freeze_candidate(env.context, aid, 1)
            with pytest.raises(ServiceError, match="停用"):
                await env.agents.check_external_boundary(env.context, original)
    changed = env.definition.model_copy(update={"instructions": "直接保存的新指令"})
    await env.agents.edit_configuration(
        env.context, aid, AgentConfigurationEdit(revision=1, definition=changed)
    )
    latest = await env.agents.resolve_current(env.context, env.body.agent_code)
    assert latest.definition.instructions == "直接保存的新指令"
    assert original.definition.instructions != latest.definition.instructions
    assert await env.agents.load_candidate(env.context, original.snapshot_id) == original
    async with env.engine.connect() as connection:
        configurations = await repository("resource_versions", env.context.scope).find(
            connection, resource_type="agent", resource_id=aid
        )
        mappings = await repository("release_mappings", env.context.scope).find(
            connection, resource_type="agent", resource_id=aid
        )
    assert len(configurations) == 1 and configurations[0]["id"] == aid
    assert configurations[0]["state"] == "CURRENT" and not mappings


async def test_migration_keeps_saved_content_without_creating_an_online_agent(agent_env):
    env = agent_env
    detail = await env.agents.create(env.context, env.body)
    aid, old_id = detail.agent.agent_id, "legacy_agent_configuration"
    table = repository("resource_versions", env.context.scope).table
    async with env.engine.connect() as connection:
        await connection.execute(
            update(table)
            .where(table.c.channel_id == env.context.scope.channel_id, table.c.id == aid)
            .values(id=old_id, state="DRAFT", version_label="旧草稿")
        )

        def migrate(sync):
            config = Config("alembic.ini")
            config.set_main_option("version_table_schema", sync.engine.url.database)
            config.attributes["connection"] = sync
            command.stamp(config, "0052_file_urls")
            command.upgrade(config, "head")

        await connection.commit()
        await connection.run_sync(migrate)
    current = await env.agents.detail(env.context, aid)
    assert current.configuration.definition == detail.configuration.definition
    assert not current.agent.online and current.agent.enabled
    async with env.engine.connect() as connection:
        old = (
            (
                await connection.execute(
                    select(table).where(
                        table.c.channel_id == env.context.scope.channel_id, table.c.id == old_id
                    )
                )
            )
            .mappings()
            .one()
        )
    assert old["state"] == "RETIRED"
    with pytest.raises(ServiceError, match="配置不存在"):
        await env.agents.raw(env.context, old_id)


async def test_migration_refuses_downgrade_with_agent_data(agent_env):
    env = agent_env
    detail = await env.agents.create(env.context, env.body)

    def downgrade(sync):
        config = Config("alembic.ini")
        config.set_main_option("version_table_schema", sync.engine.url.database)
        config.attributes["connection"] = sync
        with pytest.raises(RuntimeError, match="请使用迁移前备份"):
            command.downgrade(config, "0052_file_urls")
        assert "is_online" in {
            column["name"] for column in inspect(sync).get_columns("agent_environment_states")
        }
        assert (
            sync.exec_driver_sql("SELECT version_num FROM creativity_alembic_version").scalar()
            == "0053_agent_configuration"
        )

    async with env.engine.connect() as connection:
        await connection.run_sync(downgrade)
    assert await env.agents.detail(env.context, detail.agent.agent_id) == detail


@pytest.mark.parametrize("previous_status", ["ACTIVE", "EMERGENCY_STOP", "OFFLINE"])
async def test_migration_preserves_effective_content_and_lifecycle(agent_env, previous_status):
    env = agent_env
    detail = await env.agents.create(env.context, env.body)
    scope, aid = env.context.scope, detail.agent.agent_id
    versions = repository("resource_versions", scope).table
    states = repository("agent_environment_states", scope).table
    mappings = repository("release_mappings", scope).table
    async with env.engine.begin() as connection:
        row = await repository("resource_versions", scope).get(connection, aid)
        assert row is not None
        await connection.execute(
            versions.update()
            .where(versions.c.channel_id == scope.channel_id, versions.c.id == aid)
            .values(id="legacy_effective", state="PUBLISHED", version_label="原运行配置")
        )
        pending = {**row["content"], "instructions": "后来保存但尚未用于运行的内容"}
        await connection.execute(
            versions.insert().values(
                **{
                    **row,
                    "id": "legacy_pending",
                    "state": "DRAFT",
                    "version_label": "旧草稿",
                    "content": pending,
                    "content_digest": digest(pending),
                    "updated_at": row["updated_at"] + timedelta(seconds=1),
                }
            )
        )
        await connection.execute(
            states.insert().values(
                id=env.agents.mapping_id(env.context, aid),
                channel_id=scope.channel_id,
                environment=scope.environment,
                agent_id=aid,
                status=previous_status,
                is_online=None,
                revision=1,
                is_deleted=False,
                created_at=row["created_at"],
                updated_at=row["updated_at"],
            )
        )
        await connection.execute(
            mappings.insert().values(
                id="legacy_mapping",
                channel_id=scope.channel_id,
                environment=scope.environment,
                resource_type="agent",
                resource_id=aid,
                version_id="legacy_effective",
                revision=1,
                is_deleted=False,
                created_at=row["created_at"],
                updated_at=row["updated_at"],
            )
        )

    def migrate(sync):
        config = Config("alembic.ini")
        config.set_main_option("version_table_schema", sync.engine.url.database)
        config.attributes["connection"] = sync
        command.stamp(config, "0052_file_urls")
        command.upgrade(config, "head")
        command.upgrade(config, "head")

    async with env.engine.connect() as connection:
        await connection.run_sync(migrate)
    current = await env.agents.detail(env.context, aid)
    assert current.agent.online == (previous_status != "OFFLINE")
    assert current.agent.enabled == (previous_status != "EMERGENCY_STOP")
    assert current.configuration.definition.model_dump(mode="json") == (
        pending if previous_status == "OFFLINE" else row["content"]
    )
    async with env.engine.connect() as connection:
        assert not await repository("release_mappings", scope).find(
            connection, resource_type="agent", resource_id=aid
        )
        rows = await repository("resource_versions", scope).find(
            connection, resource_type="agent", resource_id=aid
        )
    assert {item["id"]: item["state"] for item in rows} == {
        aid: "CURRENT",
        "legacy_effective": "RETIRED",
        "legacy_pending": "RETIRED",
    }
