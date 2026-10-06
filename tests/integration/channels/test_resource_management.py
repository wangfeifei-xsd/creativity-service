"""统一资源生命周期在真实事务中执行，当前引用与历史使用互不替代。"""

import asyncio
from types import SimpleNamespace

import pytest
from sqlalchemy import select

from creativity_service.core.database import Repository, transaction
from creativity_service.core.deletion import content_key
from creativity_service.core.locking import record_key
from creativity_service.core.primitives import ServiceError, new_id
from creativity_service.core.versioning import VersionService
from creativity_service.modules.resources.configuration import (
    TABLES,
    configuration_values,
    require_dependencies,
)
from creativity_service.modules.resources.schemas import ResourceMutation
from creativity_service.modules.resources.services import ResourceManagement
from creativity_service.modules.resources.usage import record_use
from creativity_service.storage import metadata
from tests.integration.channels.conftest import provision

pytestmark = pytest.mark.integration


class ValidConfiguration:
    async def validate(self, uow, context, version, operation):
        # 此夹具隔离生命周期行为；模块内容门禁另由各模块测试验证。
        assert version.content


@pytest.fixture
async def resources(channel_env):
    env = channel_env
    from creativity_service.modules.models.assembly import ModelResourceReader
    from creativity_service.modules.prompts.authorization import PromptResourceReader
    from creativity_service.modules.skills.authorization import SkillResourceReader
    from creativity_service.modules.tools.assembly import ToolResourceReader

    for reader in (
        PromptResourceReader,
        SkillResourceReader,
        ToolResourceReader,
        ModelResourceReader,
    ):
        env.iam.authorization.resources = reader(env.engine, env.iam.authorization.resources)
    tenant = await provision(env)
    context = tenant.manager.context
    management = ResourceManagement(
        env.engine, env.iam.authorization, {k: ValidConfiguration() for k in TABLES if k != "agent"}
    )
    return SimpleNamespace(**vars(env), context=context, management=management)


async def create(env, kind, *, dependencies=(), identifier=None):
    identifier = identifier or new_id(kind)
    values = {"name": "共用资源", "status": "ACTIVE"}
    if kind == "model_route":
        values["code"] = identifier
    else:
        values.update({f"{kind}_code": identifier, "owner": "管理员"})
        if kind == "prompt":
            values["purpose"] = "验证资源管理"
        else:
            values["description"] = "验证资源管理"
        if kind == "tool":
            values["source_type"] = "builtin"
        if kind == "skill":
            values["tags"] = []
    scope = env.context.scope
    async with transaction(
        env.engine,
        scope,
        [
            content_key(scope),
            record_key(scope.channel_id, TABLES[kind], identifier),
            record_key(scope.channel_id, "resource_versions", identifier),
        ],
    ) as uow:
        await require_dependencies(uow, scope, list(dependencies))
        await Repository(metadata.tables[TABLES[kind]], scope).add(uow, identifier, values)
        await Repository(metadata.tables["resource_versions"], scope).add(
            uow,
            identifier,
            configuration_values(
                kind,
                identifier,
                {"text": "初始内容"},
                list(dependencies),
                {},
                env.context.principal_id,
            ),
        )
    return identifier


async def summary(env, kind, identifier):
    return (await env.management.summaries(env.context, kind, [identifier]))[0]


async def mutate(env, kind, identifier, operation, confirm_used=False):
    item = await summary(env, kind, identifier)
    await env.management.mutate(
        env.context,
        kind,
        identifier,
        operation,
        ResourceMutation(
            revision=item.revision,
            configuration_revision=item.configuration_revision,
            confirm_used=confirm_used,
        ),
    )


@pytest.mark.parametrize("kind", ["prompt", "model_route", "tool", "skill"])
async def test_two_states_references_usage_and_second_confirmation(resources, kind):
    env = resources
    identifier = await create(env, kind)
    assert (await summary(env, kind, identifier)).status.value == "UNPUBLISHED"
    with pytest.raises(ServiceError, match="只能关联"):
        await create(env, "skill", dependencies=[identifier])
    await mutate(env, kind, identifier, "publish")
    consumer = await create(env, "skill", dependencies=[identifier])
    item = await summary(env, kind, identifier)
    assert (item.reference_count, item.usage_count) == (1, 0)
    refs = await env.management.references(env.context, kind, identifier)
    assert refs.items[0].resource_id == consumer
    assert refs.items[0].status.value == "UNPUBLISHED"
    for operation in ("delete", "unpublish"):
        with pytest.raises(ServiceError, match="资源仍被引用"):
            await mutate(env, kind, identifier, operation, confirm_used=True)
    # 删除引用方就解除关联，历史事实仍按资源与运行去重。
    await mutate(env, "skill", consumer, "delete")
    await asyncio.gather(
        *(
            record_use(
                env.engine,
                env.context,
                "historical-run",
                kind,
                identifier,
                agent_name="业务助手",
                purpose="debug",
            )
            for _ in range(4)
        )
    )
    item = await summary(env, kind, identifier)
    assert (item.reference_count, item.usage_count) == (0, 1)
    uses = await env.management.uses(env.context, kind, identifier, 0, 20)
    assert uses.total == 1
    assert uses.items[0].caller_name == "平台管理员"
    await mutate(env, kind, identifier, "unpublish")
    with pytest.raises(ServiceError, match="二次确认"):
        await mutate(env, kind, identifier, "delete")
    await mutate(env, kind, identifier, "delete", confirm_used=True)
    assert await env.management.summaries(env.context, kind, [identifier]) == []
    table = metadata.tables["resource_uses"]
    async with env.engine.connect() as connection:
        row = (
            (
                await connection.execute(
                    select(table).where(
                        table.c.channel_id == env.context.scope.channel_id,
                        table.c.resource_id == identifier,
                    )
                )
            )
            .mappings()
            .one()
        )
        assert row["resource_name"] == "共用资源"


async def test_stale_delete_and_reference_race(resources):
    env = resources
    identifier = await create(env, "prompt")
    await mutate(env, "prompt", identifier, "publish")
    item = await summary(env, "prompt", identifier)
    body = ResourceMutation(
        revision=item.revision, configuration_revision=item.configuration_revision
    )
    results = await asyncio.gather(
        create(env, "skill", dependencies=[identifier]),
        env.management.mutate(env.context, "prompt", identifier, "delete", body),
        return_exceptions=True,
    )
    assert sum(isinstance(result, ServiceError) for result in results) == 1
    assert not any(
        isinstance(result, Exception) and not isinstance(result, ServiceError) for result in results
    )


async def test_published_edit_and_admitted_snapshot(resources):
    env = resources
    identifier = await create(env, "prompt")
    await mutate(env, "prompt", identifier, "publish")
    await create(env, "skill", dependencies=[identifier])
    versions = VersionService(env.engine, env.iam.authorization)
    scope = env.context.scope
    keys = versions.snapshot_keys(scope, "queued-run", [identifier])
    async with transaction(env.engine, scope, keys) as uow:
        snapshot = await versions.snapshot_in(
            uow, env.context, "queued-run", [identifier], "production", {}
        )
    item = await summary(env, "prompt", identifier)
    await versions.edit_draft(
        env.context, identifier, item.configuration_revision, {"text": "新任务内容"}, [], {}
    )
    assert (await summary(env, "prompt", identifier)).status.value == "PUBLISHED"
    assert snapshot.versions[0].content == {"text": "初始内容"}
    async with env.engine.connect() as connection:
        current = await Repository(metadata.tables["resource_versions"], scope).get(
            connection, identifier
        )
        saved = await Repository(metadata.tables["release_snapshots"], scope).get(
            connection, snapshot.snapshot_id
        )
    assert current["content"] == {"text": "新任务内容"}
    assert saved["versions"][0]["content"] == {"text": "初始内容"}
    with pytest.raises(ServiceError, match="修改|变更|冲突"):
        await versions.edit_draft(
            env.context, identifier, item.configuration_revision, {"text": "覆盖他人修改"}, [], {}
        )
