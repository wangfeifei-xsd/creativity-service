"""并发首次创建、锁顺序、修订冲突及组合事务回滚。"""

import asyncio

import pytest
from sqlalchemy import event, select, text

from creativity_service.core.database import Repository, assert_external_io_allowed, transaction
from creativity_service.core.database.tables import metadata
from creativity_service.core.locking import ResourceKey, acquire_locks, read_key, record_key
from creativity_service.core.primitives import ServiceError
from creativity_service.core.versioning import VersionService

pytestmark = pytest.mark.integration


async def test_reused_query_structure_keeps_scope_nulls_and_current_values(engine, context):
    base = context.scope
    foreign = base.model_copy(update={"channel_id": "another-channel"})
    subject = base.model_copy(update={"subject_type": "player", "subject_id": "one"})
    table = metadata.tables["source_links"]

    async def add(scope, identifier, source):
        async with transaction(
            engine, scope, [record_key(scope.channel_id, table.name, identifier)]
        ) as uow:
            await Repository(table, scope).add(
                uow,
                identifier,
                {
                    "source_type": "message",
                    "source_id": source,
                    "derived_type": "run",
                    "derived_id": "result",
                    "source_version": None,
                },
            )

    await add(base, "same", "base")
    await add(foreign, "same", "foreign")
    await add(subject, "scoped", "subject")
    repo = Repository(table, base)
    async with engine.connect() as connection:
        assert (await repo.get(connection, "same"))["source_id"] == "base"
        assert (await Repository(table, foreign).get(connection, "same"))["source_id"] == "foreign"
        assert not await repo.find(connection, channel_id=foreign.channel_id)
        assert set(await repo.get_many(connection, ["same", "scoped"])) == {"same"}
        assert await Repository(table, subject).get(connection, "same") is None
    async with transaction(engine, base, [record_key(base.channel_id, table.name, "same")]) as uow:
        await repo.change(uow, "same", 1, {"source_id": "changed"})
    async with engine.connect() as connection:
        assert (await repo.get(connection, "same"))["source_id"] == "changed"


async def test_pending_insert_conflict_rolls_back_all_chunks(engine, context):
    from creativity_service.core.database.inserts import InsertBatch

    scope = context.scope
    table = metadata.tables["source_links"]
    repo = Repository(table, scope)
    values = {
        "source_type": "message",
        "source_id": "source",
        "derived_type": "run",
        "derived_id": "result",
        "source_version": None,
    }
    keys = [record_key(scope.channel_id, table.name, f"batch_{index}") for index in range(101)]
    async with transaction(engine, scope, [keys[-1]]) as uow:
        await repo.add(uow, "batch_100", values)
    with pytest.raises(ServiceError, match="标识已存在"):
        async with transaction(engine, scope, keys) as uow:
            pending = InsertBatch(uow)
            await repo.add_many(
                uow, {f"batch_{index}": values for index in range(101)}, pending=pending
            )
            await pending.flush()
    async with engine.connect() as connection:
        assert [row["id"] for row in await repo.find(connection)] == ["batch_100"]


async def test_read_connection_reuses_only_current_task_and_clears_scope(engine):
    from creativity_service.core.database.reading import read_connection

    async def separate(parent):
        async with read_connection(engine) as child:
            assert child is not parent
            assert await child.scalar(select(1)) == 1

    async with read_connection(engine) as first:
        async with read_connection(engine) as reused:
            assert reused is first
        await asyncio.gather(separate(first), separate(first))
    assert first.closed
    async with read_connection(engine) as following:
        assert following is not first


async def test_shared_reads_coexist_and_exclude_mutation(engine, context):
    key = ResourceKey(context.scope.channel_id, "shared-reader", ("configuration",))
    async with transaction(engine, context.scope, [read_key(key)]) as uow:
        uow.require_read_lock(key)
        with pytest.raises(RuntimeError):
            uow.require_lock(key)
        async with engine.begin() as second:
            async with asyncio.timeout(2):
                await acquire_locks(second, frozenset([read_key(key)]))
            async with engine.begin() as writer:
                assert not await writer.scalar(
                    text("SELECT pg_try_advisory_xact_lock(:key)"), {"key": key.lock_id}
                )
        async with engine.begin() as writer:
            assert not await writer.scalar(
                text("SELECT pg_try_advisory_xact_lock(:key)"), {"key": key.lock_id}
            )
    async with transaction(engine, context.scope, [key, read_key(key)]) as uow:
        uow.require_lock(key)
        uow.require_read_lock(read_key(key))


async def test_late_locks_cannot_upgrade_reverse_order_or_cross_channel(engine, context):
    from creativity_service.modules.usage.repositories import ledger_key, platform_key

    key = ResourceKey(context.scope.channel_id, "shared-reader", ("configuration",))
    async with transaction(engine, context.scope, [read_key(key)]) as uow:
        with pytest.raises(RuntimeError, match="升级共享锁"):
            await uow.acquire([key])
        with pytest.raises(ServiceError, match="锁渠道"):
            await uow.acquire([ledger_key("another-channel")])
        await uow.acquire([ledger_key(context.scope.channel_id)])
        await uow.acquire([platform_key()])
        uow.require_lock(platform_key())
        with pytest.raises(RuntimeError, match="全局资源顺序"):
            await uow.acquire([ResourceKey(context.scope.channel_id, "earlier", ("write",))])


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


async def test_batch_locks_use_one_roundtrip_and_release_on_rollback(engine, context):
    keys = [ResourceKey(context.scope.channel_id, "batch-lock", (str(i),)) for i in range(10)]
    queries = []

    def record(connection, cursor, statement, parameters, execution, many):
        if "pg_advisory_xact_lock(" in statement:
            queries.append(statement)

    event.listen(engine.sync_engine, "before_cursor_execute", record)
    try:
        with pytest.raises(RuntimeError, match="验证回滚"):
            async with transaction(engine, context.scope, list(reversed(keys))):
                async with engine.begin() as contender:
                    for key in keys:
                        assert not await contender.scalar(
                            text("SELECT pg_try_advisory_xact_lock(:key)"), {"key": key.lock_id}
                        )
                raise RuntimeError("验证回滚")
        assert len(queries) == 1
        async with engine.begin() as contender:
            for key in keys:
                assert await contender.scalar(
                    text("SELECT pg_try_advisory_xact_lock(:key)"), {"key": key.lock_id}
                )
    finally:
        event.remove(engine.sync_engine, "before_cursor_execute", record)


async def test_batch_source_links_keep_constant_queries_and_atomic_rejection(
    engine, context, authorization
):
    from creativity_service.core.deletion import (
        ContentRef,
        DeletionGuard,
        DeletionService,
        content_key,
    )
    from creativity_service.core.locking import record_key
    from tests.integration.agents.test_read_queries import statements

    guard = DeletionGuard(context.scope)
    counts = []
    source = ContentRef("version", "source")
    for size in (1, 10):
        identifiers = [f"bulk_{size}_{i}" for i in range(size)]
        keys = [content_key(context.scope)] + [
            record_key(context.scope.channel_id, "source_links", identifier)
            for identifier in identifiers
        ]
        with statements(engine) as queries:
            async with transaction(engine, context.scope, keys) as uow:
                await guard.link_many(
                    uow,
                    [
                        (identifier, source, ContentRef("snapshot", identifier), None)
                        for identifier in identifiers
                    ],
                )
        counts.append(len(queries))
    assert counts[0] == counts[1]

    await DeletionService(engine, authorization).mark(context, source, "USER_REQUEST")
    keys = [content_key(context.scope)] + [
        record_key(context.scope.channel_id, "source_links", identifier)
        for identifier in ("clean_link", "blocked_link")
    ]
    with pytest.raises(ServiceError) as rejected:
        async with transaction(engine, context.scope, keys) as uow:
            await guard.link_many(
                uow,
                [
                    (
                        "clean_link",
                        ContentRef("version", "clean"),
                        ContentRef("snapshot", "new"),
                        None,
                    ),
                    ("blocked_link", source, ContentRef("snapshot", "new"), None),
                ],
            )
    assert rejected.value.code == "CONTENT_DELETED"
    async with engine.connect() as connection:
        assert not await guard.sources.get_many(connection, ["clean_link", "blocked_link"])


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
