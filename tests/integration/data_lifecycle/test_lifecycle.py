"""删除全图、重试、跨渠道隔离、迟到上传与旧库恢复的组合验收。"""

import asyncio
from datetime import timedelta

import pytest
from sqlalchemy import delete, insert, select, update

from creativity_service.core.database import transaction
from creativity_service.core.deletion import ContentRef, DeletionGuard, content_key
from creativity_service.core.primitives import ServiceError, utcnow
from creativity_service.modules.data_lifecycle.recovery import (
    backup_manifest,
    block_restore,
    replay_restore,
)
from creativity_service.modules.data_lifecycle.repository import rows, scope_of, worker_context
from creativity_service.modules.memory.schemas import MemoryCreate
from creativity_service.storage import metadata
from tests.integration.memory.test_memory import candidate

pytestmark = pytest.mark.integration


async def drain(service, channel_id):
    for _ in range(20):
        if await service.sweep(channel_id, 2) == 0:
            break
    async with service.engine.connect() as connection:
        failed = await rows(connection, channel_id, "deletion_work_items", state="FAILED")
    for item in failed:
        await service.handlers.registry.clean(
            worker_context(scope_of(item)), ContentRef(item["target_type"], item["target_id"])
        )


async def test_full_graph_removes_contents_and_preserves_independent_memory(env, lifecycle):
    dependent = await env.memory.write_candidate(env.context, env.run_id, candidate(env))
    independent = await env.memory.create(env.context, MemoryCreate(key="play_style", value="休闲"))
    async with env.engine.begin() as connection:
        table = metadata.tables["memories"]
        await connection.execute(
            update(table)
            .where(
                table.c.channel_id == env.context.scope.channel_id,
                table.c.id == dependent.memory_id,
            )
            .values(subject_name="来源中的主体名称")
        )
    artifact = await env.conversations.artifacts.upload(
        env.context,
        "敏感附件.txt",
        "text/plain",
        b"private content",
        [ContentRef("run", env.run_id)],
    )
    job = await env.conversations.delete(env.context, env.cid)
    await drain(lifecycle, env.context.scope.channel_id)
    progress = await lifecycle.progress(env.context, job.deletion_id)
    assert progress["status"] == "COMPLETED", progress
    assert progress["completed"] == progress["total"]
    assert progress["proof_digests"]
    async with env.engine.connect() as connection:
        for name in (
            "messages",
            "conversation_summaries",
            "context_snapshots",
            "run_contents",
            "checkpoints",
            "run_events",
        ):
            assert await rows(connection, env.context.scope.channel_id, name) == []
        assert (await rows(connection, env.context.scope.channel_id, "conversations"))[0][
            "status"
        ] == "DELETED"
        memories = {
            r["id"]: r for r in await rows(connection, env.context.scope.channel_id, "memories")
        }
        assert memories[dependent.memory_id]["value"] is None
        assert memories[dependent.memory_id]["subject_name"] is None
        assert memories[independent.memory_id]["value"] == "休闲"
        assert (
            await rows(
                connection, env.context.scope.channel_id, "artifacts", id=artifact.artifact_id
            )
        )[0]["state"] == "DELETED"
    assert env.conversations.artifacts.store.data == {}
    await drain(lifecycle, env.context.scope.channel_id)
    with pytest.raises(ServiceError, match="删除"):
        await env.conversations.artifacts.download(env.context, artifact.artifact_id)


async def test_cleanup_failure_keeps_guard_and_retry_is_idempotent(env, lifecycle):
    artifact = await env.conversations.artifacts.upload(
        env.context, "文件.txt", "text/plain", b"private", [ContentRef("run", env.run_id)]
    )
    store = env.conversations.artifacts.store
    original = store.delete

    async def failing(key):
        raise OSError("不可写入原文到错误状态")

    store.delete = failing
    job = await env.conversations.delete(env.context, env.cid)
    await lifecycle.sweep(env.context.scope.channel_id)
    progress = await lifecycle.progress(env.context, job.deletion_id)
    assert progress["status"] == "FAILED"
    assert progress["proof_digests"] == []
    with pytest.raises(ServiceError):
        await env.conversations.artifacts.download(env.context, artifact.artifact_id)
    store.delete = original
    await lifecycle.retry(env.context, job.deletion_id)
    await asyncio.gather(
        lifecycle.sweep(env.context.scope.channel_id), lifecycle.sweep(env.context.scope.channel_id)
    )
    assert (await lifecycle.progress(env.context, job.deletion_id))["status"] == "COMPLETED"
    async with env.engine.connect() as connection:
        items = await rows(
            connection, env.context.scope.channel_id, "deletion_work_items", job_id=job.deletion_id
        )
    assert max(i["attempts"] for i in items) == 2
    assert all(i["last_error"] is None for i in items)


async def test_late_upload_cannot_recreate_deleted_object(env, lifecycle):
    started, finish = asyncio.Event(), asyncio.Event()
    store = env.conversations.artifacts.store
    original = store.put

    async def late(key, data, content_type):
        started.set()
        await finish.wait()
        await original(key, data, content_type)

    store.put = late
    upload = asyncio.create_task(
        env.conversations.artifacts.upload(
            env.context, "迟到导出.txt", "text/plain", b"private", [ContentRef("run", env.run_id)]
        )
    )
    await started.wait()
    await env.conversations.delete(env.context, env.cid)
    await drain(lifecycle, env.context.scope.channel_id)
    finish.set()
    with pytest.raises(ServiceError) as error:
        await upload
    assert error.value.code == "CONTENT_DELETED"
    assert store.data == {}


async def test_restore_old_database_and_objects_replays_latest_manifest(env, lifecycle):
    artifact = await env.conversations.artifacts.upload(
        env.context, "恢复附件.txt", "text/plain", b"private", [ContentRef("run", env.run_id)]
    )
    channel_id = env.context.scope.channel_id
    async with env.engine.connect() as connection:
        snapshot = {name: await rows(connection, channel_id, name) for name in metadata.tables}
    objects = dict(env.conversations.artifacts.store.data)
    await env.conversations.delete(env.context, env.cid)
    await drain(lifecycle, channel_id)
    manifest = await backup_manifest(lifecycle, channel_id)
    await block_restore(channel_id)
    async with env.engine.begin() as connection:
        for name, old in snapshot.items():
            table = metadata.tables[name]
            await connection.execute(delete(table).where(table.c.channel_id == channel_id))
            if old:
                await connection.execute(insert(table), old)
    env.conversations.artifacts.store.data.update(objects)
    with pytest.raises(ServiceError) as error:
        await env.conversations.artifacts.download(env.context, artifact.artifact_id)
    assert error.value.code == "RECOVERY_BLOCKED"
    proof = await replay_restore(lifecycle, channel_id, manifest["manifest"]["sequence"])
    assert proof["proof_digests"]
    assert env.conversations.artifacts.store.data == {}
    async with env.engine.connect() as connection:
        assert await rows(connection, channel_id, "run_contents") == []
    with pytest.raises(ServiceError) as error:
        await env.conversations.artifacts.download(env.context, artifact.artifact_id)
    assert error.value.code == "CONTENT_DELETED"


async def test_foreign_channel_same_ids_are_untouched(env, lifecycle):
    channel_id = env.context.scope.channel_id
    other = channel_id + "b"
    async with env.engine.begin() as connection:
        for name in ("conversations", "messages", "runs", "run_contents", "source_links"):
            table = metadata.tables[name]
            copied = [
                dict(r)
                for r in (
                    await connection.execute(select(table).where(table.c.channel_id == channel_id))
                ).mappings()
            ]
            if copied:
                await connection.execute(
                    insert(table), [{**r, "channel_id": other} for r in copied]
                )
    await env.conversations.delete(env.context, env.cid)
    await drain(lifecycle, channel_id)
    async with env.engine.connect() as connection:
        assert (await rows(connection, other, "conversations"))[0]["status"] == "ACTIVE"
        assert await rows(connection, other, "messages")
        assert await rows(connection, other, "run_contents")


async def test_memory_clear_finishes_legacy_job_and_old_worker_is_blocked(env, lifecycle):
    saved = await env.memory.write_candidate(env.context, env.run_id, candidate(env))
    await env.memory.select(env.context, env.run_id, ["usual_budget"], [])
    job = await env.memory.forget(env.context, saved.memory_id)
    await drain(lifecycle, env.context.scope.channel_id)
    assert (await env.memory.deletion(env.context, job.deletion_id)).status == "COMPLETED"
    with pytest.raises(ServiceError):
        async with transaction(
            env.engine, env.context.scope, [content_key(env.context.scope)]
        ) as uow:
            await DeletionGuard(env.context.scope).check(
                uow, [ContentRef("memory", saved.memory_id)]
            )


async def test_expired_worker_lease_is_reclaimed(env, lifecycle):
    job = await env.conversations.delete(env.context, env.cid)
    await lifecycle.prepare_channel(env.context.scope.channel_id)
    table = metadata.tables["deletion_work_items"]
    async with env.engine.begin() as connection:
        await connection.execute(
            update(table)
            .where(table.c.channel_id == env.context.scope.channel_id)
            .values(
                state="RUNNING",
                lease_token="dead_worker",
                lease_until=utcnow() - timedelta(seconds=1),
            )
        )
    await drain(lifecycle, env.context.scope.channel_id)
    assert (await lifecycle.progress(env.context, job.deletion_id))["status"] == "COMPLETED"


async def test_shared_memory_recomputes_sources_without_losing_independent_value(env, lifecycle):
    from tests.integration.memory.test_memory import budget

    dependent = await env.memory.write_candidate(env.context, env.run_id, candidate(env))
    independent = await env.memory.create(
        env.context, MemoryCreate(key="usual_budget", value=budget())
    )
    assert independent.memory_id == dependent.memory_id
    await env.conversations.delete(env.context, env.cid)
    await drain(lifecycle, env.context.scope.channel_id)
    value = (await env.memory.detail(env.context, independent.memory_id)).memory
    assert value.status == "ACTIVE" and value.value == budget()
    assert all(source.name != "偏好咨询" for source in value.sources)


async def test_recovery_rejects_stale_or_unavailable_ledger(env, lifecycle, monkeypatch, tmp_path):
    await env.conversations.delete(env.context, env.cid)
    manifest = await backup_manifest(lifecycle, env.context.scope.channel_id)
    await block_restore(env.context.scope.channel_id)
    with pytest.raises(ServiceError) as failure:
        await replay_restore(
            lifecycle, env.context.scope.channel_id, manifest["manifest"]["sequence"] + 1
        )
    assert failure.value.code == "RECOVERY_PROOF_INVALID"
    monkeypatch.setenv("CREATIVITY_DELETION_LEDGER_PATH", str(tmp_path / "missing"))
    with pytest.raises(ServiceError) as failure:
        await replay_restore(lifecycle, env.context.scope.channel_id, 0)
    assert failure.value.code == "DELETION_LEDGER_UNAVAILABLE"


async def test_real_orphan_objects_and_cache_remain_channel_scoped(env, lifecycle):
    from creativity_service.core.artifacts import S3ObjectStore
    from creativity_service.core.config import Settings
    from creativity_service.core.contracts import ToolResult
    from creativity_service.core.infrastructure import Infrastructure
    from creativity_service.modules.data_lifecycle.objects import sweep_objects
    from creativity_service.modules.tools.execution import RedisToolCache
    from creativity_service.modules.tools.repositories import ToolRepository
    from creativity_service.modules.tools.schemas import ToolExecution

    infrastructure = Infrastructure(Settings())
    store = S3ObjectStore(infrastructure.s3, infrastructure.bucket)
    channel_id = env.context.scope.channel_id
    own = f"channels/{channel_id}/orphan"
    foreign = f"channels/{channel_id}b/orphan"
    cache = RedisToolCache(
        infrastructure.redis_clients["redis_cache"], "lifecycle_test", env.engine
    )
    key = channel_id + ":content"
    try:
        await store.put(own, b"private", "text/plain")
        await store.put(foreign, b"other", "text/plain")
        lifecycle.handlers.store = store
        assert await sweep_objects(lifecycle, channel_id, restoring=True) == 1
        assert await store.get(foreign, 100) == b"other"
        value = ToolResult(
            scope=env.context.scope,
            tool_version_id="tool",
            source_request_id="remote",
            source_version="1",
            observed_at=utcnow(),
            data={"secret": "private"},
            evidence_refs=(),
            warnings=(),
            cursor=None,
            has_more=False,
            truncated=False,
            coverage={},
        )
        await cache.put_for_run(key, value, 60, env.run_id)
        assert await cache.get(key) == value
        cached, source_run_id = await cache.get_with_source(key)
        reused = await env.runs.admit_run(env.context, env.request, "cached-result")
        call = ToolExecution(
            run_id=reused.run_id, step_id="cached-step", tool_version_id="tool", arguments={}
        )
        await ToolRepository(env.engine).record(
            env.context,
            call,
            "cached-call",
            "tool",
            "CACHED",
            None,
            cached,
            None,
            0,
            {},
            source_run_id=source_run_id,
        )
        await env.conversations.delete(env.context, env.cid)
        assert await cache.get(key) is None
        with pytest.raises(ServiceError) as failure:
            async with transaction(
                env.engine, env.context.scope, [content_key(env.context.scope)]
            ) as uow:
                await DeletionGuard(env.context.scope).check(
                    uow, [ContentRef("run", reused.run_id)]
                )
        assert failure.value.code == "CONTENT_DELETED"
        with pytest.raises(ServiceError):
            await cache.put_for_run(key, value, 60, env.run_id)
        assert (
            await infrastructure.redis_clients["redis_cache"].get("lifecycle_test:tools:" + key)
            is None
        )
    finally:
        await store.delete(own)
        await store.delete(foreign)
        await cache.delete(key)
        await infrastructure.close()


async def test_scope_deletion_crosses_subjects_but_keeps_shared_configuration(env, lifecycle):
    from creativity_service.core.deletion import DeletionService, RecoveryService

    other = env.context.model_copy(
        update={"scope": env.context.scope.model_copy(update={"subject_id": "another-user"})}
    )
    await RecoveryService(env.engine, env.authorization).initialize_fresh(other)
    artifact = await env.conversations.artifacts.upload(
        other, "派生文件.txt", "text/plain", b"private", [ContentRef("run", env.run_id)]
    )
    independent = await env.conversations.artifacts.upload(
        other,
        "独立文件.txt",
        "text/plain",
        b"independent",
        [ContentRef("version", env.resolver.definition.version_ids[0])],
    )
    await DeletionService(env.engine, env.authorization).mark(env.context, None, "SUBJECT_DELETED")
    with pytest.raises(ServiceError) as failure:
        await env.conversations.artifacts.download(other, artifact.artifact_id)
    assert failure.value.code == "CONTENT_DELETED"
    assert (await env.conversations.artifacts.download(other, independent.artifact_id))[
        0
    ] == b"independent"
    await drain(lifecycle, env.context.scope.channel_id)
    async with env.engine.connect() as connection:
        versions = await rows(connection, env.context.scope.channel_id, "resource_versions")
        assert versions and all(row["state"] == "PUBLISHED" for row in versions)
        assert (
            await rows(
                connection, env.context.scope.channel_id, "artifacts", id=artifact.artifact_id
            )
        )[0]["state"] == "DELETED"
    assert (await env.conversations.artifacts.download(other, independent.artifact_id))[
        0
    ] == b"independent"


async def test_memory_refresh_before_planning_cannot_revive_derived_file(env, lifecycle):
    saved = await env.memory.write_candidate(env.context, env.run_id, candidate(env))
    artifact = await env.conversations.artifacts.upload(
        env.context,
        "记忆文件.txt",
        "text/plain",
        b"private memory",
        [ContentRef("memory", saved.memory_id)],
    )
    job = await env.conversations.delete(env.context, env.cid)
    assert (await env.memory.detail(env.context, saved.memory_id)).memory.status == "REVOKED"
    with pytest.raises(ServiceError) as failure:
        await env.conversations.artifacts.download(env.context, artifact.artifact_id)
    assert failure.value.code == "CONTENT_DELETED"
    await drain(lifecycle, env.context.scope.channel_id)
    assert (await lifecycle.progress(env.context, job.deletion_id))["status"] == "COMPLETED"
    assert env.conversations.artifacts.store.data == {}


async def test_backup_initialization_preserves_concurrent_restore_barrier(
    env, lifecycle, monkeypatch
):
    from creativity_service.core.deletion.ledger import DeletionLedger

    channel_id = env.context.scope.channel_id
    await backup_manifest(lifecycle, channel_id)
    original = DeletionLedger.operate

    async def concurrent_block(self, channel_id, **kwargs):
        if kwargs.get("initialize"):
            await original(self, channel_id, blocked=True, required=True)
        return await original(self, channel_id, **kwargs)

    monkeypatch.setattr(DeletionLedger, "operate", concurrent_block)
    snapshot = await backup_manifest(lifecycle, channel_id)
    assert snapshot["manifest"]["blocked"]
    assert (await original(DeletionLedger(), channel_id, required=True))["blocked"]


async def test_single_message_blocks_queued_run_and_cleans_turn_input(env, lifecycle):
    from creativity_service.core.deletion import DeletionService

    await DeletionService(env.engine, env.authorization).mark(
        env.context, ContentRef("message", env.source.source_id), "MESSAGE_DELETED"
    )
    with pytest.raises(ServiceError) as failure:
        async with transaction(
            env.engine, env.context.scope, [content_key(env.context.scope)]
        ) as uow:
            await DeletionGuard(env.context.scope).check(uow, [ContentRef("run", env.run_id)])
    assert failure.value.code == "CONTENT_DELETED"
    await drain(lifecycle, env.context.scope.channel_id)
    async with env.engine.connect() as connection:
        assert not await rows(
            connection, env.context.scope.channel_id, "run_contents", run_id=env.run_id
        )
        turns = await rows(
            connection, env.context.scope.channel_id, "conversation_turns", run_id=env.run_id
        )
        assert turns and all(t["input"] == {} and t["confirmed_conditions"] == {} for t in turns)
        assert (await rows(connection, env.context.scope.channel_id, "conversations", id=env.cid))[
            0
        ]["status"] == "ACTIVE"


async def test_missing_independent_manifest_keeps_existing_database_closed(
    env, monkeypatch, tmp_path
):
    from creativity_service.core.deletion.ledger import DeletionLedger

    monkeypatch.setenv("CREATIVITY_DELETION_LEDGER_PATH", str(tmp_path / "lost-volume"))
    with pytest.raises(ServiceError) as failure:
        async with transaction(
            env.engine, env.context.scope, [content_key(env.context.scope)]
        ) as uow:
            await DeletionGuard(env.context.scope).check(uow, [ContentRef("run", env.run_id)])
    assert failure.value.code == "DELETION_LEDGER_UNAVAILABLE"
    with pytest.raises(ServiceError):
        await DeletionLedger().record(
            env.context.scope, "run", env.run_id, "CONTENT_REQUESTED", "test"
        )


async def test_empty_memory_clear_reports_completion(env, lifecycle):
    job = await env.memory.clear(env.context)
    assert job.status == "COMPLETED"
    progress = await lifecycle.progress(env.context, job.deletion_id)
    assert progress["status"] == "COMPLETED" and progress["total"] == 0


async def test_restore_cannot_reopen_a_newer_barrier(env, lifecycle, monkeypatch):
    from creativity_service.core.deletion.ledger import DeletionLedger

    channel_id = env.context.scope.channel_id
    await env.conversations.delete(env.context, env.cid)
    await drain(lifecycle, channel_id)
    backup = await backup_manifest(lifecycle, channel_id)
    await block_restore(channel_id)
    original = DeletionLedger.operate

    async def newer_barrier(self, channel_id, **kwargs):
        if kwargs.get("blocked") is False:
            await original(self, channel_id, blocked=True, required=True)
        return await original(self, channel_id, **kwargs)

    monkeypatch.setattr(DeletionLedger, "operate", newer_barrier)
    with pytest.raises(ServiceError) as failure:
        await replay_restore(lifecycle, channel_id, backup["manifest"]["sequence"])
    assert failure.value.code == "RECOVERY_PROOF_INVALID"
    assert (await original(DeletionLedger(), channel_id, required=True))["blocked"]
