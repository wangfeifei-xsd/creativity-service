"""并发首次创建、锁顺序、修订冲突及组合事务回滚。"""

import asyncio

import pytest
from sqlalchemy import select

from creativity_service.core.database import Repository, assert_external_io_allowed, transaction
from creativity_service.core.database.tables import metadata
from creativity_service.core.locking import ResourceKey
from creativity_service.core.primitives import ServiceError
from creativity_service.core.versioning import VersionService

pytestmark = pytest.mark.integration


async def draft(service, context, label="第一版"):
    return await service.create_draft(
        context, "agent", "agent_one", label, {"instruction": "测试"}, [], {"type": "object"}
    )


async def test_concurrent_first_create(engine, context, authorization, validator):
    service = VersionService(engine, authorization, validator)
    results = await asyncio.gather(
        *(draft(service, context) for _ in range(8)), return_exceptions=True
    )
    assert sum(not isinstance(result, Exception) for result in results) == 1
    assert all(
        not isinstance(result, Exception)
        or isinstance(result, ServiceError)
        and result.code == "VERSION_LABEL_CONFLICT"
        for result in results
    )
    async with engine.connect() as connection:
        rows = await Repository(metadata.tables["resource_versions"], context.scope).find(
            connection
        )
    assert len(rows) == 1


async def test_multiple_locks_sorted_and_scope_isolated(engine, context):
    first = ResourceKey(context.scope.channel_id, "resource", ("first",))
    second = ResourceKey(context.scope.channel_id, "resource", ("second",))
    order = []

    async def take(keys, name):
        async with transaction(engine, context.scope, keys):
            order.append((name, "start"))
            await asyncio.sleep(0.02)
            order.append((name, "end"))

    async with asyncio.timeout(10):
        await asyncio.gather(
            take([first, second], "http"),
            take([second, first], "worker"),
            take([first, second], "compensation"),
        )
    assert all(order[i][0] == order[i + 1][0] for i in range(0, 6, 2))


async def test_revision_conflict_and_published_content_frozen(
    engine, context, authorization, validator
):
    service = VersionService(engine, authorization, validator)
    version = await draft(service, context)
    results = await asyncio.gather(
        *(
            service.edit_draft(context, version.version_id, 1, {"writer": i}, [], {})
            for i in range(2)
        ),
        return_exceptions=True,
    )
    assert (
        sum(
            isinstance(result, ServiceError) and result.code == "REVISION_CONFLICT"
            for result in results
        )
        == 1
    )
    published = await service.freeze(context, version.version_id, 2)
    assert published.state == "PUBLISHED"
    with pytest.raises(ServiceError, match="发布版本不能修改"):
        await service.edit_draft(context, version.version_id, 3, {}, [], {})


async def test_snapshot_rolls_back_with_caller_transaction(
    engine, context, authorization, validator
):
    service = VersionService(engine, authorization, validator)
    version = await draft(service, context)
    ids = [version.version_id]
    with pytest.raises(RuntimeError, match="模拟预算失败"):
        async with transaction(
            engine, context.scope, service.snapshot_keys(context.scope, "run_rollback", ids)
        ) as uow:
            await service.snapshot_in(
                uow, context, "run_rollback", ids, "debug", {"type": "object"}
            )
            with pytest.raises(RuntimeError, match="外部调用"):
                assert_external_io_allowed()
            raise RuntimeError("模拟预算失败")
    async with engine.connect() as connection:
        assert (
            await Repository(metadata.tables["release_snapshots"], context.scope).find(connection)
            == []
        )
    assert_external_io_allowed()


async def test_snapshot_unchanged_after_edit_and_release_conflict(
    engine, context, authorization, validator
):
    service = VersionService(engine, authorization, validator)
    version = await draft(service, context)
    ids = [version.version_id]
    async with transaction(
        engine, context.scope, service.snapshot_keys(context.scope, "run_old", ids)
    ) as uow:
        snapshot = await service.snapshot_in(
            uow, context, "run_old", ids, "debug", {"type": "object"}
        )
    await service.edit_draft(context, version.version_id, 1, {"instruction": "新的草稿"}, [], {})
    assert snapshot.versions[0].content == {"instruction": "测试"}
    await service.freeze(context, version.version_id, 2)
    results = await asyncio.gather(
        *(service.release(context, version.version_id, None, "首次发布") for _ in range(2)),
        return_exceptions=True,
    )
    assert (
        sum(
            isinstance(result, ServiceError) and result.code == "REVISION_CONFLICT"
            for result in results
        )
        == 1
    )
    async with engine.connect() as connection:
        rows = (
            (
                await connection.execute(
                    select(metadata.tables["release_snapshots"]).where(
                        metadata.tables["release_snapshots"].c.id == snapshot.snapshot_id
                    )
                )
            )
            .mappings()
            .all()
        )
        assert rows[0]["versions"][0]["content"] == {"instruction": "测试"}


async def test_missing_validator_and_foreign_channel_dependency_rejected(
    engine, context, authorization, validator
):
    service = VersionService(engine, authorization)
    version = await draft(service, context)
    with pytest.raises(ServiceError, match="校验服务"):
        await service.freeze(context, version.version_id, 1)
    bad = await VersionService(engine, authorization, validator).create_draft(
        context, "agent", "other", "第一版", {}, ["foreign_version"], {}
    )
    with pytest.raises(ServiceError, match="同渠道"):
        await VersionService(engine, authorization, validator).freeze(context, bad.version_id, 1)


async def test_configuration_deletion_covers_other_environments_and_snapshot(
    engine, context, authorization, validator
):
    from creativity_service.core.deletion import ContentRef, DeletionService, RecoveryService

    service = VersionService(engine, authorization, validator)
    version = await draft(service, context)
    ids = [version.version_id]
    async with transaction(
        engine, context.scope, service.snapshot_keys(context.scope, "run_deleted", ids)
    ) as uow:
        snapshot = await service.snapshot_in(
            uow, context, "run_deleted", ids, "debug", {"type": "object"}
        )
    alternate = context.model_copy(
        update={"scope": context.scope.model_copy(update={"environment": "prod"})}
    )
    await RecoveryService(engine, authorization).initialize_fresh(alternate)
    assert (await service.read_version(alternate, version.version_id)).content == {
        "instruction": "测试"
    }
    await DeletionService(engine, authorization).mark(
        context, ContentRef("agent", "agent_one"), "USER_REQUEST"
    )
    with pytest.raises(ServiceError, match="已删除"):
        await service.read_version(alternate, version.version_id)
    with pytest.raises(ServiceError, match="已删除"):
        await service.read_snapshot(context, snapshot.snapshot_id)


async def test_reject_repeatable_read_snapshot_for_mutual_exclusion(engine, context):
    strict = engine.execution_options(isolation_level="REPEATABLE READ")
    with pytest.raises(RuntimeError, match="READ COMMITTED"):
        async with transaction(
            strict, context.scope, [ResourceKey(context.scope.channel_id, "item", ("new",))]
        ):
            pytest.fail("旧快照隔离级别不能进入写入协议")
