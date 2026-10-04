"""独立依据保留和跨主体评测内容清理使用生产生命周期链。"""

import pytest

from creativity_service.core.deletion import ContentRef, DeletionService
from creativity_service.core.primitives import ServiceError, utcnow
from creativity_service.modules.data_lifecycle.handlers import ContentHandlers
from creativity_service.modules.data_lifecycle.services import DataLifecycleService
from creativity_service.modules.evaluations.repositories import repository
from creativity_service.modules.evaluations.schemas import (
    CaseInput,
    DatasetCreate,
    DatasetVersionInput,
)
from tests.integration.channels.test_prompts_http import MemoryStore
from tests.integration.data_lifecycle.test_lifecycle import drain
from tests.integration.evaluations.test_evaluations import advance, prepare

pytestmark = [
    pytest.mark.integration,
    pytest.mark.parametrize(
        "agent_env",
        [
            {
                "environment": "prod",
                "independent_actions": ["release:publish", "data:read_sensitive"],
            }
        ],
        indirect=True,
    ),
]


async def test_equivalent_independent_source_survives_deletion_and_report_changes(evaluation_env):
    env = evaluation_env
    _, _, task = await prepare(env, count=2)
    for _ in range(3):
        await advance(env, task)
    async with env.engine.connect() as connection:
        results = await repository("evaluation_results", env.context.scope).find(
            connection, evaluation_id=task.evaluation_id
        )
    source_ids = [r["run_id"] for r in results]
    assert len(source_ids) == 2 and all(source_ids)
    dataset = await env.evaluations.create_dataset(
        env.context,
        DatasetCreate(
            name="独立依据", scenario="通用验证", owner="审阅者", applicability="独立来源等价核对"
        ),
    )
    case = CaseInput(
        case_key="independent",
        title="样本",
        input={"request": "验证输入"},
        source_mode="independent",
        source_refs=[
            {"resource_type": "run", "resource_id": identifier} for identifier in source_ids
        ],
        assertions=[
            {"kind": "equal", "name": "内容", "path": "data.answer", "expected": "验证完成"}
        ],
        label_source="审阅",
    )
    dataset = await env.evaluations.create_version(
        env.context,
        dataset.dataset_id,
        DatasetVersionInput(
            revision=dataset.revision, version_label="初版", captured_at=utcnow(), cases=[case]
        ),
    )
    with pytest.raises(ServiceError) as invalid:
        await env.evaluations.create_version(
            env.context,
            dataset.dataset_id,
            DatasetVersionInput(
                revision=dataset.revision,
                version_label="不充分依据",
                captured_at=utcnow(),
                cases=[
                    case.model_copy(
                        update={
                            "assertions": [
                                case.assertions[0].model_copy(
                                    update={"expected": "无来源支持的文本"}
                                )
                            ]
                        }
                    )
                ],
            ),
        )
    assert invalid.value.code == "INDEPENDENT_SOURCE_INVALID"
    service = DataLifecycleService(
        env.engine, ContentHandlers(env.engine, MemoryStore(), env.runs), env.iam.authorization
    )
    source_context = await env.runs.access_context(env.context, source_ids[0])
    await DeletionService(env.engine, env.iam.authorization).mark(
        source_context, ContentRef("run", source_ids[0]), "CONTENT_REQUESTED"
    )
    await drain(service, env.context.scope.channel_id)
    current = await env.evaluations.dataset(env.context, dataset.dataset_id)
    retained = current.versions[-1].cases[0]
    assert retained.valid
    assert [r.resource_id for r in retained.payload.source_refs] == [source_ids[1]]
    assert retained.payload.input == {"request": "验证输入"}
    assert retained.payload.assertions[0].name == "独立依据断言 1"
    assert (await env.runs.get_run(env.context, source_ids[1])).state == "SUCCEEDED"
    report = await env.evaluations.comparison(env.context, task.evaluation_id)
    assert not report.reproducible
    with pytest.raises(ServiceError):
        await env.runs.get_run(env.context, source_ids[0])


async def test_retention_windows_and_channel_override(evaluation_env):
    from datetime import timedelta

    from sqlalchemy import update

    from creativity_service.modules.data_lifecycle.repository import rows
    from creativity_service.modules.data_lifecycle.retention import scan_retention
    from creativity_service.storage import metadata

    env = evaluation_env
    _, _, task = await prepare(env, count=1)
    await advance(env, task)
    service = DataLifecycleService(
        env.engine, ContentHandlers(env.engine, MemoryStore(), env.runs), env.iam.authorization
    )
    channel_id = env.context.scope.channel_id
    async with env.engine.begin() as connection:
        events = metadata.tables["run_events"]
        await connection.execute(
            update(events)
            .where(events.c.channel_id == channel_id)
            .values(created_at=utcnow() - timedelta(hours=25))
        )
    result = await scan_retention(service, channel_id)
    assert result["metadata_removed"] > 0
    async with env.engine.connect() as connection:
        assert await rows(connection, channel_id, "run_contents", kind="input")
        assert not await rows(connection, channel_id, "run_events")
    async with env.engine.begin() as connection:
        runs = metadata.tables["runs"]
        channels = metadata.tables["channels"]
        await connection.execute(
            update(runs)
            .where(runs.c.channel_id == channel_id)
            .values(created_at=utcnow() - timedelta(days=31))
        )
        await connection.execute(
            update(channels)
            .where(channels.c.channel_id == channel_id)
            .values(retention_policy={"retention_days": 90, "run_content_days": 60})
        )
    assert (await scan_retention(service, channel_id))["registered"] == 0
    async with env.engine.begin() as connection:
        await connection.execute(
            update(channels)
            .where(channels.c.channel_id == channel_id)
            .values(retention_policy={"retention_days": 90}, status="SUSPENDED")
        )
    assert (await scan_retention(service, channel_id))["registered"] >= 1
    await drain(service, channel_id)
    async with env.engine.connect() as connection:
        assert not await rows(connection, channel_id, "run_contents", kind="input")
        assert await rows(connection, channel_id, "usage_records")


async def test_metadata_retention_keeps_pending_and_recent_usage(evaluation_env):
    from datetime import timedelta

    from sqlalchemy import insert, update

    from creativity_service.modules.data_lifecycle.repository import rows
    from creativity_service.modules.data_lifecycle.retention import scan_retention
    from creativity_service.storage import metadata

    env = evaluation_env
    _, _, task = await prepare(env, count=1)
    await advance(env, task)
    service = DataLifecycleService(
        env.engine, ContentHandlers(env.engine, MemoryStore(), env.runs), env.iam.authorization
    )
    channel_id = env.context.scope.channel_id
    old = utcnow() - timedelta(days=366)
    async with env.engine.begin() as connection:
        usage = (await rows(connection, channel_id, "usage_records"))[0]
        assert usage["state"] == "SETTLED"
        table = metadata.tables["usage_records"]
        await connection.execute(
            update(table)
            .where(table.c.channel_id == channel_id, table.c.id == usage["id"])
            .values(updated_at=old)
        )
        await connection.execute(
            insert(table),
            [
                {
                    **usage,
                    "id": "old_pending",
                    "attempt_id": "pending_attempt",
                    "state": "PENDING",
                    "updated_at": old,
                },
                {
                    **usage,
                    "id": "recent_settled",
                    "attempt_id": "recent_attempt",
                    "updated_at": utcnow() - timedelta(days=364),
                },
            ],
        )
        audit = (await rows(connection, channel_id, "audit_events"))[0]
        table = metadata.tables["audit_events"]
        await connection.execute(
            update(table)
            .where(table.c.channel_id == channel_id, table.c.id == audit["id"])
            .values(updated_at=old)
        )
    await scan_retention(service, channel_id)
    async with env.engine.connect() as connection:
        assert not await rows(connection, channel_id, "usage_records", id=usage["id"])
        assert not await rows(
            connection, channel_id, "usage_events", attempt_id=usage["attempt_id"]
        )
        assert not await rows(
            connection, channel_id, "budget_reservations", attempt_id=usage["attempt_id"]
        )
        assert await rows(connection, channel_id, "usage_records", id="old_pending")
        assert await rows(connection, channel_id, "usage_records", id="recent_settled")
        assert not await rows(connection, channel_id, "audit_events", id=audit["id"])


async def test_archived_channel_reclaims_shared_configuration_after_retention(evaluation_env):
    from datetime import timedelta

    from sqlalchemy import update

    from creativity_service.modules.data_lifecycle.repository import rows
    from creativity_service.modules.data_lifecycle.retention import scan_retention
    from creativity_service.storage import metadata

    env = evaluation_env
    await prepare(env, count=1)
    service = DataLifecycleService(
        env.engine, ContentHandlers(env.engine, MemoryStore(), env.runs), env.iam.authorization
    )
    channel_id = env.context.scope.channel_id
    async with env.engine.begin() as connection:
        table = metadata.tables["channels"]
        await connection.execute(
            update(table)
            .where(table.c.channel_id == channel_id)
            .values(status="ARCHIVED", archived_at=utcnow() - timedelta(days=91))
        )
    assert (await scan_retention(service, channel_id))["registered"] > 0
    await drain(service, channel_id)
    async with env.engine.connect() as connection:
        versions = await rows(connection, channel_id, "resource_versions")
        assert versions and all(v["content"] == {} and v["state"] == "RETIRED" for v in versions)
        assert not await rows(connection, channel_id, "deletion_work_items", state="FAILED")


async def test_http_deletion_preview_progress_and_scope_rejection(evaluation_env):
    env = evaluation_env
    _, _, task = await prepare(env, count=1)
    await advance(env, task)
    async with env.engine.connect() as connection:
        result = (
            await repository("evaluation_results", env.context.scope).find(
                connection, evaluation_id=task.evaluation_id
            )
        )[0]
    service = DataLifecycleService(
        env.engine, ContentHandlers(env.engine, MemoryStore(), env.runs), env.iam.authorization
    )
    env.client._transport.app.state.data_lifecycle = service
    body = {"resource_type": "run", "resource_id": result["run_id"]}
    preview = await env.client.post("/admin/v1/data-lifecycle/deletion-preview", json=body)
    assert preview.status_code == 200, preview.text
    assert any(r["label"] == "运行内容" for r in preview.json()["resources"])
    rejected = await env.client.post(
        "/admin/v1/data-lifecycle/deletions", json={**body, "channel_id": "another-channel"}
    )
    assert rejected.status_code == 422
    response = await env.client.post("/admin/v1/data-lifecycle/deletions", json=body)
    assert response.status_code == 202, response.text
    deletion_id = response.json()["deletion_id"]
    await drain(service, env.context.scope.channel_id)
    progress = await env.client.get(f"/admin/v1/deletions/{deletion_id}/progress")
    assert progress.status_code == 200 and progress.json()["status"] == "COMPLETED", progress.text
    assert progress.json()["proof_digests"]


async def test_retention_reads_only_bounded_due_candidates(evaluation_env):
    from datetime import timedelta

    from sqlalchemy import insert

    from creativity_service.modules.data_lifecycle.repository import rows
    from creativity_service.modules.data_lifecycle.retention import scan_retention
    from creativity_service.storage import metadata
    from tests.integration.agents.test_read_queries import statements

    env = evaluation_env
    _, _, task = await prepare(env, count=1)
    await advance(env, task)
    service = DataLifecycleService(
        env.engine, ContentHandlers(env.engine, MemoryStore(), env.runs), env.iam.authorization
    )
    channel_id = env.context.scope.channel_id
    async with env.engine.begin() as connection:
        run = (await rows(connection, channel_id, "runs"))[0]
        await connection.execute(
            insert(metadata.tables["runs"]),
            [
                {
                    **run,
                    "id": f"retention_history_{i:04}",
                    "created_at": utcnow() - timedelta(days=40 if i < 15 else 1),
                }
                for i in range(300)
            ],
        )
    with statements(env.engine) as captured:
        assert (await scan_retention(service, channel_id, limit=5))["registered"] == 5
    async with env.engine.connect() as connection:
        marked = await rows(connection, channel_id, "deletion_markers", target_type="run")
    assert {r["target_id"] for r in marked} == {f"retention_history_{i:04}" for i in range(5)}
    assert (await scan_retention(service, channel_id, limit=5))["registered"] == 5
    async with env.engine.connect() as connection:
        marked = await rows(connection, channel_id, "deletion_markers", target_type="run")
    assert {r["target_id"] for r in marked} == {f"retention_history_{i:04}" for i in range(10)}
    candidates = [
        q
        for q in captured
        if q.lstrip().startswith("SELECT")
        and any(
            f"FROM {name}" in q
            for name in ("runs", "memories", "conversations", "artifacts", "usage_exports")
        )
    ]
    assert candidates and all("LIMIT" in q or " IN (" in q for q in candidates)
    marker_reads = [
        q
        for q in captured
        if q.lstrip().startswith("SELECT") and "FROM deletion_markers" in q and "FROM runs" not in q
    ]
    assert all("LIMIT" in q or " IN (" in q or "EXISTS" in q for q in marker_reads)
