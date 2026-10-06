"""MEM-A01—A06/A08 的属性检索、来源删除、并发及使用前复核。"""

import asyncio
from datetime import timedelta

import pytest
from sqlalchemy import update

from creativity_service.core.database import transaction
from creativity_service.core.deletion import ContentRef, DeletionService, RecoveryService
from creativity_service.core.primitives import ServiceError, utcnow
from creativity_service.modules.memory import repositories as repo
from creativity_service.modules.memory.schemas import (
    CandidateInput,
    MemoryConfirm,
    MemoryCreate,
    MemoryPolicy,
    MemoryUpdate,
    PolicyInput,
    PreferenceInput,
)
from creativity_service.modules.memory.tables import metadata

pytestmark = pytest.mark.integration


def budget(value=100):
    return {"min": value, "max": value + 100, "currency": "CNY"}


def candidate(env, intent="EXPLICIT", value=100):
    return CandidateInput(key="usual_budget", value=budget(value), intent=intent, source=env.source)


async def select(env, **kwargs):
    return await env.memory.select(
        env.context, env.run_id, kwargs.get("keys", ["usual_budget"]), kwargs.get("current", [])
    )


async def load(env, selection, **kwargs):
    return await env.memory.load(
        env.context, env.run_id, selection, kwargs.get("current", []), kwargs.get("required", [])
    )


async def rows(env, name, **filters):
    async with env.engine.connect() as connection:
        return await repo.rows(connection, name, env.context.scope, **filters)


async def test_mem_a01_task_only_and_inference_never_become_fact(env):
    assert (
        await env.memory.write_candidate(env.context, env.run_id, candidate(env, "TASK_ONLY"))
        is None
    )
    assert await rows(env, "memories") == []
    proposed = await env.memory.write_candidate(env.context, env.run_id, candidate(env, "INFERRED"))
    assert proposed.status == "PROPOSED"
    assert (await select(env)).refs == []
    confirmed = await env.memory.confirm(
        env.context, proposed.memory_id, MemoryConfirm(revision=proposed.revision)
    )
    assert confirmed.status == "ACTIVE"
    assert (await load(env, await select(env))).items[0].value == budget()


async def test_mem_a02_explicit_save_source_and_current_priority(env):
    saved = await env.memory.write_candidate(env.context, env.run_id, candidate(env))
    assert (await env.memory.detail(env.context, saved.memory_id)).memory.sources[
        0
    ].name == "偏好咨询"
    selected = await select(env)
    assert (await load(env, selected)).items[0].version_id == saved.version_id
    assert (await load(env, selected, current=["usual_budget"])).items == []
    assert (await select(env, current=["usual_budget"])).refs == []
    assert (await env.memory.detail(env.context, saved.memory_id)).memory.usage_count == 1


async def test_mem_a03_a08_identical_subjects_do_not_cross_scopes_or_clear(env):
    saved = await env.memory.create(env.context, MemoryCreate(key="usual_budget", value=budget()))
    original_key = env.memory.reference_cache_key(env.context, "agent_one", ["usual_budget"])
    for changed in (
        {"channel_id": "foreign"},
        {"environment": "prod"},
        {"subject_type": "member"},
        {"subject_id": "other-user"},
    ):
        foreign = env.context.model_copy(
            update={"scope": env.context.scope.model_copy(update=changed)}
        )
        await RecoveryService(env.engine, env.authorization).initialize_fresh(foreign)
        if changed.get("channel_id"):
            from tests.integration.memory.conftest import business_attributes

            await env.memory.set_policy(
                foreign, PolicyInput(revision=0, attributes=business_attributes())
            )
        own = await env.memory.create(foreign, MemoryCreate(key="usual_budget", value=budget(900)))
        assert (
            env.memory.reference_cache_key(foreign, "agent_one", ["usual_budget"]) != original_key
        )
        assert (await env.memory.list_memories(foreign)).items[0].value == budget(900)
        with pytest.raises(ServiceError) as error:
            await env.memory.detail(foreign, saved.memory_id)
        assert error.value.status == 404
        await env.memory.clear(foreign)
        assert (await env.memory.detail(foreign, own.memory_id)).memory.value is None
        assert (await env.memory.detail(env.context, saved.memory_id)).memory.value == budget()


async def test_mem_a04_forget_blocks_queued_refs_and_history_and_tracks_cleanup(env):
    saved = await env.memory.create(env.context, MemoryCreate(key="usual_budget", value=budget()))
    selected = await select(env)
    job = await env.memory.forget(env.context, saved.memory_id)
    assert (await env.memory.forget(env.context, saved.memory_id)).deletion_id == job.deletion_id
    assert not (await load(env, selected)).items
    detail = await env.memory.detail(env.context, saved.memory_id)
    assert detail.memory.value is None and detail.memory.sources == []
    assert all(v["value"] is None for v in await rows(env, "memory_versions"))
    await env.memory.clean(env.context, ContentRef("memory", saved.memory_id))
    assert (await env.memory.deletion(env.context, job.deletion_id)).status == "WAITING_PROPAGATION"
    assert await rows(env, "memory_sources") == []


async def test_mem_a05_deleted_source_is_unusable_before_cleanup(env):
    saved = await env.memory.write_candidate(env.context, env.run_id, candidate(env))
    selected = await select(env)
    await DeletionService(env.engine, env.authorization).mark(
        env.context, ContentRef("message", env.source.source_id), "SOURCE_DELETED"
    )
    # 来源已进入原运行输入，旧运行整体阻断，不能借记忆加载继续恢复。
    with pytest.raises(ServiceError) as failure:
        await load(env, selected)
    assert failure.value.code == "CONTENT_DELETED"
    detail = await env.memory.detail(env.context, saved.memory_id)
    assert detail.memory.status == "REVOKED" and detail.memory.value is None
    env.run_id = (await env.runs.admit_run(env.context, env.request, "new-memory-read")).run_id
    assert not (await select(env)).refs
    assert all(v["value"] is None for v in await rows(env, "memory_versions"))


async def test_independent_source_survives_without_deleted_source_or_history_content(env):
    saved = await env.memory.write_candidate(env.context, env.run_id, candidate(env))
    independent = await env.memory.create(
        env.context, MemoryCreate(key="usual_budget", value=budget())
    )
    assert independent.memory_id == saved.memory_id
    await DeletionService(env.engine, env.authorization).mark(
        env.context, ContentRef("message", env.source.source_id), "SOURCE_DELETED"
    )
    await env.memory.reconcile_source(env.context, ContentRef("message", env.source.source_id))
    detail = await env.memory.detail(env.context, saved.memory_id)
    assert detail.memory.status == "ACTIVE"
    assert [s.name for s in detail.memory.sources] == ["人工修正"]
    serialized = detail.model_dump_json()
    assert "偏好咨询" not in serialized
    assert all(v["value"] is None for v in (await rows(env, "memory_versions"))[:-1])
    with pytest.raises(ServiceError) as failure:
        await select(env)
    assert failure.value.code == "CONTENT_DELETED"
    # 独立依据由新运行读取，原来源运行仍保持不可恢复。
    env.run_id = (
        await env.runs.admit_run(env.context, env.request, "independent-memory-read")
    ).run_id
    assert (await load(env, await select(env))).items[0].value == budget()


async def test_mem_a06_disable_preserves_context_clear_preserves_switch(env):
    saved = await env.memory.write_candidate(env.context, env.run_id, candidate(env))
    selected = await select(env)
    prefs = await env.memory.set_preferences(
        env.context, PreferenceInput(enabled=False, revision=0)
    )
    assert not (await load(env, selected)).items
    assert (
        await env.memory.write_candidate(env.context, env.run_id, candidate(env, value=500)) is None
    )
    assert (await env.memory.detail(env.context, saved.memory_id)).memory.value == budget()
    assert (await env.conversations.messages(env.context, env.cid)).items[0].text
    await env.memory.set_preferences(
        env.context, PreferenceInput(enabled=True, revision=prefs.revision)
    )
    assert (await load(env, await select(env))).items
    await env.memory.clear(env.context)
    assert (await env.memory.get_preferences(env.context)).enabled
    assert not (await select(env)).refs


async def test_concurrent_corrections_conflict_and_one_active_attribute(env):
    saved = await env.memory.create(env.context, MemoryCreate(key="usual_budget", value=budget()))
    outcomes = await asyncio.gather(
        *(
            env.memory.update(
                env.context, saved.memory_id, MemoryUpdate(revision=saved.revision, value=budget(v))
            )
            for v in [200, 300]
        ),
        return_exceptions=True,
    )
    assert (
        sum(
            isinstance(result, ServiceError) and result.code == "REVISION_CONFLICT"
            for result in outcomes
        )
        == 1
    )
    assert len(await rows(env, "memories", status="ACTIVE")) == 1
    outcomes = await asyncio.gather(
        *(
            env.memory.create(env.context, MemoryCreate(key="usual_budget", value=budget(v)))
            for v in [600, 700]
        )
    )
    assert len(await rows(env, "memories", status="ACTIVE")) == 1
    assert len({o.version_id for o in outcomes}) == 2
    assert any(r["status"] == "SUPERSEDED" for r in await rows(env, "memories"))


async def test_capacity_is_atomic_and_agent_cannot_enlarge_policy(env):
    await env.memory.set_policy(
        env.context, PolicyInput(revision=1, max_items=1, retrieval_limit=1)
    )
    outcomes = await asyncio.gather(
        *(
            env.memory.create(env.context, MemoryCreate(key=k, value="休闲"))
            for k in ["play_style", "preferred_hours"]
        ),
        return_exceptions=True,
    )
    assert (
        sum(isinstance(o, ServiceError) and o.code == "MEMORY_LIMIT_REACHED" for o in outcomes) == 1
    )
    with pytest.raises(ServiceError) as error:
        await env.memory.validate_agent_policy(
            env.context, MemoryPolicy(max_items=2, retrieval_limit=1)
        )
    assert error.value.code == "MEMORY_POLICY_EXCEEDS_CHANNEL"


@pytest.mark.parametrize(
    "body,code",
    [
        ({"key": "balance", "value": 10}, "MEMORY_ATTRIBUTE_NOT_ALLOWED"),
        ({"key": "price", "value": 10}, "MEMORY_ATTRIBUTE_NOT_ALLOWED"),
        (
            {"key": "usual_budget", "value": budget(), "memory_type": "SECRET"},
            "MEMORY_TYPE_NOT_ALLOWED",
        ),
        ({"key": "play_style", "value": "password: hidden"}, "MEMORY_SENSITIVE_VALUE"),
        (
            {
                "key": "usual_budget",
                "value": {"min": 100, "max": 200, "currency": "CNY", "api_key": "secret"},
            },
            "MEMORY_VALUE_INVALID",
        ),
        (
            {"key": "membership_level", "memory_type": "FACT", "value": "高级会员"},
            "MEMORY_AUTHORITY_REQUIRED",
        ),
    ],
)
async def test_sensitive_ephemeral_and_unverified_facts_rejected(env, body, code):
    with pytest.raises(ServiceError) as error:
        await env.memory.create(env.context, MemoryCreate(**body))
    assert error.value.code == code


async def test_expiration_and_replacement_invalidate_old_selection(env):
    saved = await env.memory.create(env.context, MemoryCreate(key="usual_budget", value=budget()))
    selected = await select(env)
    await env.memory.create(env.context, MemoryCreate(key="usual_budget", value=budget(400)))
    assert not (await load(env, selected)).items
    current = await select(env)
    table = metadata.tables["memories"]
    async with transaction(env.engine, env.context.scope, repo.keys(env.context.scope)) as uow:
        await uow.connection.execute(
            update(table)
            .where(
                table.c.channel_id == env.context.scope.channel_id,
                table.c.id == current.refs[0].memory_id,
            )
            .values(expires_at=utcnow() - timedelta(seconds=1))
        )
    assert not (await load(env, current)).items
    assert (await env.memory.detail(env.context, saved.memory_id)).memory.status == "SUPERSEDED"


async def test_degrade_records_warning_and_auth_errors_never_degrade(env, monkeypatch):
    await env.memory.create(env.context, MemoryCreate(key="usual_budget", value=budget()))

    async def unavailable(*args):
        raise ServiceError("MEMORY_STORE_UNAVAILABLE", "模拟来源存储故障", 503)

    monkeypatch.setattr(env.memory, "refresh", unavailable)
    selected = await select(env)
    assert not selected.refs and selected.warnings
    assert (await rows(env, "memory_retrievals"))[0]["warnings"]
    assert (await load(env, selected, required=["membership_level"])).required_tool_keys == [
        "membership_level"
    ]
    await env.memory.set_policy(
        env.context, PolicyInput(revision=1, failure_mode="FAIL"), "agent_one"
    )
    with pytest.raises(ServiceError, match="存储故障"):
        await select(env)
    env.authorization.denied = True
    with pytest.raises(ServiceError) as error:
        await select(env)
    assert error.value.status == 403


async def test_management_scope_restore_and_cursor_binding(env):
    one = await env.memory.create(env.context, MemoryCreate(key="usual_budget", value=budget()))
    await env.memory.create(env.context, MemoryCreate(key="play_style", value="休闲"))
    manager = env.context.model_copy(
        update={
            "scope": env.context.scope.model_copy(update={"subject_type": None, "subject_id": None})
        }
    )
    assert len((await env.memory.list_memories(manager)).items) == 2
    assert (await env.memory.detail(manager, one.memory_id)).memory.memory_id == one.memory_id
    with pytest.raises(ServiceError, match="主体"):
        await env.memory.clear(manager)
    page = await env.memory.list_memories(env.context, limit=1)
    other = env.context.model_copy(
        update={"scope": env.context.scope.model_copy(update={"environment": "prod"})}
    )
    with pytest.raises(ServiceError) as error:
        await env.memory.list_memories(other, cursor=page.next_cursor)
    assert error.value.code == "CURSOR_INVALID"
    await env.memory.clear(manager, one.memory_id)
    assert not (await env.memory.list_memories(env.context)).items


async def test_unconfigured_agent_and_policy_reduction_disable_queued_reads(env):
    await env.memory.create(env.context, MemoryCreate(key="usual_budget", value=budget()))
    selected = await select(env)
    await env.memory.set_policy(env.context, PolicyInput(revision=1, read_enabled=False))
    assert not (await load(env, selected)).items
    with pytest.raises(ServiceError) as error:
        await env.memory.set_policy(env.context, PolicyInput(revision=1), "agent_one")
    assert error.value.code == "MEMORY_POLICY_EXCEEDS_CHANNEL"


async def test_source_title_permission_and_missing_source_never_expose_content(env):
    saved = await env.memory.write_candidate(env.context, env.run_id, candidate(env))
    original = env.authorization.require

    async def limited(context, action, resource_id):
        if action == "conversation:read":
            raise ServiceError("FORBIDDEN", "来源不可见", 403)
        await original(context, action, resource_id)

    env.authorization.require = limited
    detail = await env.memory.detail(env.context, saved.memory_id)
    assert detail.memory.sources[0].name == "来源名称不可用"
    assert "偏好咨询" not in detail.model_dump_json()


async def test_actual_conversation_deletion_preserves_independent_memory(env):
    sourced = await env.memory.write_candidate(env.context, env.run_id, candidate(env))
    await env.memory.create(env.context, MemoryCreate(key="usual_budget", value=budget()))
    from creativity_service.modules.memory.schemas import SourceInput

    proposed = await env.memory.write_candidate(
        env.context,
        env.run_id,
        CandidateInput(
            key="play_style",
            value="竞技",
            intent="INFERRED",
            source=SourceInput(**env.source.model_dump()),
        ),
    )
    impact = await env.conversations.preview_delete(env.context, env.cid)
    assert impact.shared_memories == 1 and impact.exclusive_memories == 1
    await env.conversations.delete(env.context, env.cid)
    await env.memory.clean(env.context, ContentRef("memory", sourced.memory_id))
    await env.memory.clean(env.context, ContentRef("memory", proposed.memory_id))
    assert (await env.memory.detail(env.context, sourced.memory_id)).memory.status == "ACTIVE"
    assert (await env.memory.detail(env.context, proposed.memory_id)).memory.value is None
    assert (await env.memory.detail(env.context, sourced.memory_id)).memory.sources[
        0
    ].name == "人工修正"


async def test_trusted_fact_value_matches_recorded_tool_result_and_requires_current_source(env):
    from creativity_service.core.contracts import EvidenceLocation, EvidenceRef, ToolResult
    from creativity_service.core.versioning import VersionService
    from creativity_service.modules.tools.repositories import ToolRepository
    from creativity_service.modules.tools.schemas import ToolExecution
    from tests.integration.core.conftest import TestVersionValidator

    versions = VersionService(env.engine, env.authorization, TestVersionValidator())
    version = await versions.create_draft(
        env.context, "tool", "membership_tool", "会员资料初版", {}, [], {"type": "object"}
    )
    result = ToolResult(
        scope=env.context.scope,
        tool_version_id=version.version_id,
        source_request_id="request_one",
        source_version="v1",
        observed_at=utcnow(),
        data={"membership_level": "高级会员"},
        warnings=(),
        cursor=None,
        has_more=False,
        truncated=False,
        coverage={},
        evidence_refs=(
            EvidenceRef(
                evidence_id="membership_evidence",
                scope=env.context.scope,
                source_type="tool_call",
                source_id="membership_call",
                source_version="v1",
                observed_at=utcnow(),
                location=EvidenceLocation(
                    field_path=("membership_level",), text_start=None, text_end=None
                ),
                title="会员资料查询",
                authorized_actions=("read",),
            ),
        ),
    )
    call = ToolExecution(
        run_id=env.run_id, step_id="step_fact", tool_version_id=version.version_id, arguments={}
    )
    await ToolRepository(env.engine).record(
        env.context,
        call,
        "membership_call",
        "membership_tool",
        "SUCCEEDED",
        None,
        result,
        None,
        10,
        {"principal_id": env.context.principal_id},
    )
    with pytest.raises(ServiceError):
        await env.memory.write_fact(
            env.context,
            env.run_id,
            "membership_level",
            result.model_copy(update={"data": {"membership_level": "伪造等级"}}),
        )
    fact = await env.memory.write_fact(env.context, env.run_id, "membership_level", result)
    assert fact.status == "ACTIVE" and fact.memory_type == "FACT"
    selected = await select(env, keys=["membership_level"])
    assert (await load(env, selected)).items[0].value == "高级会员"
    mandatory = await load(env, selected, required=["membership_level"])
    assert mandatory.items == [] and mandatory.required_tool_keys == ["membership_level"]
    await DeletionService(env.engine, env.authorization).mark(
        env.context, ContentRef("tool_call", "membership_call"), "TOOL_REVOKED"
    )
    with pytest.raises(ServiceError) as failure:
        await load(env, selected)
    assert failure.value.code == "CONTENT_DELETED"
    assert (await env.memory.detail(env.context, fact.memory_id)).memory.status == "REVOKED"
    env.run_id = (await env.runs.admit_run(env.context, env.request, "new-fact-read")).run_id
    assert not (await select(env, keys=["membership_level"])).refs


async def test_queued_candidates_cannot_reintroduce_forgotten_or_cleared_content(env):
    saved = await env.memory.write_candidate(env.context, env.run_id, candidate(env))
    await env.memory.forget(env.context, saved.memory_id)
    assert (
        await env.memory.write_candidate(env.context, env.run_id, candidate(env, value=800)) is None
    )
    await env.memory.clear(env.context)
    assert (
        await env.memory.write_candidate(
            env.context,
            env.run_id,
            CandidateInput(key="play_style", value="休闲", intent="EXPLICIT", source=env.source),
        )
        is None
    )
    assert not (await env.memory.list_memories(env.context)).items


async def test_reenable_does_not_revive_old_selection_or_old_automatic_write(env):
    await env.memory.write_candidate(env.context, env.run_id, candidate(env))
    selected = await select(env)
    disabled = await env.memory.set_preferences(
        env.context, PreferenceInput(enabled=False, revision=0)
    )
    await env.memory.set_preferences(
        env.context, PreferenceInput(enabled=True, revision=disabled.revision)
    )
    assert not (await load(env, selected)).items
    assert (
        await env.memory.write_candidate(env.context, env.run_id, candidate(env, value=800)) is None
    )
    assert (await load(env, await select(env))).items


async def test_new_run_cannot_relearn_from_forgotten_old_source_and_reclear_advances_cutoff(env):
    saved = await env.memory.write_candidate(env.context, env.run_id, candidate(env))
    await env.memory.forget(env.context, saved.memory_id)
    first = await env.memory.clear(env.context)
    later = await env.runs.admit_run(env.context, env.request, "after-forgetting")
    assert await env.memory.write_candidate(env.context, later.run_id, candidate(env)) is None
    second = await env.memory.clear(env.context)
    assert first.deletion_id != second.deletion_id and first.requested_at < second.requested_at
    assert (
        await env.memory.write_candidate(env.context, later.run_id, candidate(env, value=300))
        is None
    )


async def test_channel_attributes_are_configured_and_not_a_platform_business_catalog(env):
    from creativity_service.modules.memory.schemas import MemoryAttribute

    custom = MemoryAttribute(
        key="writing_format",
        label="写作格式",
        value_schema={"type": "string", "enum": ["条目", "段落"]},
    )
    policy = await env.memory.get_policy(env.context)
    await env.memory.set_policy(
        env.context, PolicyInput(revision=policy.revision, attributes=[custom])
    )
    value = await env.memory.create(env.context, MemoryCreate(key="writing_format", value="条目"))
    assert value.display_name == "写作格式" and value.layer == "profile"
    with pytest.raises(ServiceError, match="不属于"):
        await env.memory.create(env.context, MemoryCreate(key="play_style", value="休闲"))
    other = env.context.model_copy(
        update={"scope": env.context.scope.model_copy(update={"channel_id": "new_generic_channel"})}
    )
    await RecoveryService(env.engine, env.authorization).initialize_fresh(other)
    attributes = (await env.memory.get_policy(other)).attributes
    assert {a.key for a in attributes} == {
        "preferred_language",
        "communication_style",
        "interests",
        "time_preferences",
    }
    assert (await env.memory.list_memories(other)).items == []
    with pytest.raises(ServiceError):
        await env.memory.set_policy(
            env.context,
            PolicyInput(
                revision=policy.revision + 1,
                attributes=[
                    custom.model_copy(
                        update={"value_schema": {"$dynamicRef": "https://invalid.example/schema"}}
                    )
                ],
            ),
        )
