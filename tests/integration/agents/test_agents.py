"""AGT-A01/A02/A04/A05 的配置、发布与受理快照验证，不代表真实模型评测。"""

import asyncio
from datetime import timedelta

import pytest

from creativity_service.core.database import transaction
from creativity_service.core.primitives import ServiceError, digest, new_id, utcnow
from creativity_service.core.versioning import VersionService
from creativity_service.modules.agents.repositories import repository
from creativity_service.modules.agents.runtime import AgentRunResolver
from creativity_service.modules.agents.schemas import (
    AgentReleaseInput,
    AgentStateInput,
    AgentTestInput,
    AgentValidateInput,
    AgentVersionEdit,
)
from creativity_service.modules.releases.ports import EvaluationEvidence
from creativity_service.modules.releases.services import ReleaseService
from tests.integration.channels.conftest import provision

pytestmark = pytest.mark.integration


async def publish(env, detail, **changes):
    version = next(v for v in detail.versions if v.status.value == "DRAFT")
    body = AgentReleaseInput(
        version_id=version.version_id,
        revision=version.revision,
        environment=env.context.scope.environment,
        expected_mapping_revision=detail.release_revision,
        note="验证发布",
    )
    return await ReleaseService(env.agents).release(
        env.context, detail.agent.agent_id, body.model_copy(update=changes)
    )


async def test_agt_a04_code_revision_and_mapping_races(agent_env):
    env = agent_env
    results = await asyncio.gather(
        *(env.agents.create(env.context, env.body) for _ in range(2)), return_exceptions=True
    )
    assert (
        sum(isinstance(r, ServiceError) and r.code == "AGENT_CODE_CONFLICT" for r in results) == 1
    )
    detail = next(r for r in results if not isinstance(r, Exception))
    version = detail.versions[0]
    edits = await asyncio.gather(
        *(
            env.agents.edit_version(
                env.context,
                version.version_id,
                AgentVersionEdit(revision=1, definition=env.definition),
            )
            for _ in range(2)
        ),
        return_exceptions=True,
    )
    assert sum(isinstance(r, ServiceError) and r.code == "REVISION_CONFLICT" for r in edits) == 1
    detail = await env.agents.detail(env.context, detail.agent.agent_id)
    releases = await asyncio.gather(
        *(publish(env, detail) for _ in range(2)), return_exceptions=True
    )
    assert sum(isinstance(r, ServiceError) and r.code == "REVISION_CONFLICT" for r in releases) == 1, [str(r) for r in releases]
    current = await env.agents.detail(env.context, detail.agent.agent_id)
    assert len(current.releases) == 1 and len(current.versions) == 2


async def test_agt_a02_release_switch_preserves_admitted_snapshot(agent_env):
    env = agent_env
    detail = await env.agents.create(env.context, env.body)
    first = await publish(env, detail)
    frozen = await env.agents.resolve_published(env.context, env.body.agent_code)
    draft = next(v for v in first.versions if v.status.value == "DRAFT")
    changed = env.definition.model_copy(
        update={"limits": env.definition.limits.model_copy(update={"max_model_rounds": 4})}
    )
    await env.agents.edit_version(
        env.context, draft.version_id, AgentVersionEdit(revision=draft.revision, definition=changed)
    )
    await publish(env, await env.agents.detail(env.context, detail.agent.agent_id))
    latest = await env.agents.resolve_published(env.context, env.body.agent_code)
    assert latest.source_version_id != frozen.source_version_id
    assert (
        frozen.definition.limits.max_model_rounds == 6
        and latest.definition.limits.max_model_rounds == 4
    )
    # 受理夹具使用公共事务及快照写入，17 才负责模型执行和用量估算。
    run_id = new_id("run")
    resolver = AgentRunResolver(env.agents)
    definition = resolver.definition(frozen)
    versions = VersionService(env.engine)
    keys = versions.snapshot_keys(
        env.context.scope, run_id, list(definition.version_ids)
    ) + resolver.keys(env.context, definition)
    async with transaction(env.engine, env.context.scope, keys) as uow:
        await resolver.validate_in(uow, env.context, definition)
        snapshot = await versions.snapshot_in(
            uow,
            env.context,
            run_id,
            list(definition.version_ids),
            "production",
            definition.output_schema,
            frozen_versions=frozen.versions,
        )
    assert snapshot.versions[0].version_id == frozen.source_version_id
    assert snapshot.versions[0].content["limits"]["max_model_rounds"] == 6
    with pytest.raises(ServiceError) as exc:
        await env.agents.edit_version(
            env.context,
            frozen.source_version_id,
            AgentVersionEdit(revision=1, definition=env.definition),
        )
    assert exc.value.code == "VERSION_FROZEN"


async def test_candidate_draft_revision_and_no_runtime_field_override(agent_env):
    env = agent_env
    detail = await env.agents.create(env.context, env.body)
    draft = detail.versions[0]
    candidate = await env.agents.freeze_candidate(env.context, draft.version_id, 1, "evaluation")
    candidate.definition.input_schema["properties"].clear()
    assert "request" in candidate.definition.input_schema["properties"]
    await env.agents.edit_version(
        env.context,
        draft.version_id,
        AgentVersionEdit(
            revision=1,
            definition=env.definition.model_copy(
                update={"limits": env.definition.limits.model_copy(update={"token_limit": 20000})}
            ),
        ),
    )
    assert (
        await env.agents.load_candidate(env.context, candidate.snapshot_id)
    ).definition.limits.token_limit == 16000
    with pytest.raises(ServiceError) as exc:
        await env.agents.check_external_boundary(
            env.context, candidate.model_copy(update={"agent_name": "替换名称"})
        )
    assert exc.value.code == "SNAPSHOT_INVALID"
    with pytest.raises(ServiceError) as exc:
        await env.agents.test(
            env.context,
            draft.version_id,
            AgentTestInput(revision=2, input={"request": "说明"}, idempotency_key="test-1"),
        )
    assert exc.value.code == "DEPENDENCY_UNAVAILABLE"


async def test_agt_a01_missing_dependencies_capabilities_and_channel_isolation(agent_env):
    env = agent_env
    bad = env.definition.model_copy(
        update={
            "bindings": env.definition.bindings.model_copy(
                update={"skill_versions": ("missing_skill",)}
            )
        }
    )
    detail = await env.agents.create(env.context, env.body.model_copy(update={"definition": bad}))
    result = await env.agents.validate(
        env.context,
        detail.versions[0].version_id,
        AgentValidateInput(revision=1, purpose="production"),
    )
    assert not result.valid and any(
        i.code == "DEPENDENCY_INVALID" for c in result.checks for i in c.issues
    )
    with pytest.raises(ServiceError):
        await publish(env, detail)
    await env.agents.edit_version(
        env.context,
        detail.versions[0].version_id,
        AgentVersionEdit(revision=1, definition=env.definition),
    )
    # 清除模型能力证据模拟当前配置尚未验证，不能由 Agent 发布绕过。
    from creativity_service.core.locking import record_key
    from creativity_service.modules.models.repositories import model_key

    async with transaction(
        env.engine,
        env.context.scope,
        [
            model_key(env.context.scope.channel_id),
            record_key(env.context.scope.channel_id, "models", env.model.id),
        ],
    ) as uow:
        repo = repository("models", env.context.scope)
        row = await repo.get(uow.connection, env.model.id)
        await repo.change(uow, env.model.id, row["revision"], {"capabilities": {}})
    result = await env.agents.validate(
        env.context,
        detail.versions[0].version_id,
        AgentValidateInput(revision=2, purpose="production"),
    )
    assert not result.valid and any(
        i.code == "CAPABILITY_MISMATCH" for c in result.checks for i in c.issues
    )
    other = await provision(env, "other", "playmate")
    with pytest.raises(ServiceError):
        await env.agents.detail(other.manager.context, detail.agent.agent_id)
    response = await env.client.post(
        "/admin/v1/agents", json={**env.body.model_dump(mode="json"), "channel_id": "other"}
    )
    assert response.status_code == 422


async def test_prod_without_matching_evaluation_is_blocked_and_digests_survive_publish(agent_env):
    env = agent_env
    detail = await env.agents.create(env.context, env.body)
    version = detail.versions[0]
    candidate = await env.agents.freeze_candidate(env.context, version.version_id, 1, "evaluation")
    # 将生产环境标志用于门禁的单元边界验证，真实发布仍保持受信工作区验证。
    from creativity_service.modules.releases.checks import check_evidence

    with pytest.raises(ServiceError) as exc:
        check_evidence(
            None,
            env.context,
            detail.agent.agent_id,
            candidate.content_digest,
            candidate.dependencies_digest,
            (),
        )
    assert exc.value.code == "EVALUATION_REQUIRED"
    evidence = EvaluationEvidence(
        channel_id=env.context.scope.channel_id,
        agent_id=detail.agent.agent_id,
        environment="test",
        content_digest=candidate.content_digest,
        dependencies_digest=candidate.dependencies_digest,
        report_digest=digest(["fixture-report"]),
        report_ids=("report_fixture",),
        passed=True,
        expires_at=utcnow() + timedelta(hours=1),
    )
    check_evidence(
        evidence,
        env.context,
        detail.agent.agent_id,
        candidate.content_digest,
        candidate.dependencies_digest,
        ("report_fixture",),
    )
    for broken in (
        evidence.model_copy(update={"passed": False}),
        evidence.model_copy(update={"dependencies_digest": "f" * 64}),
        evidence.model_copy(update={"content_digest": "e" * 64}),
    ):
        with pytest.raises(ServiceError) as exc:
            check_evidence(
                broken,
                env.context,
                detail.agent.agent_id,
                candidate.content_digest,
                candidate.dependencies_digest,
                ("report_fixture",),
            )
        assert exc.value.code == "EVALUATION_STALE"
    await publish(env, detail)
    final = await env.agents.resolve_published(env.context, env.body.agent_code)
    assert final.source_version_id != candidate.source_version_id
    assert final.candidate_digest == candidate.candidate_digest


async def test_offline_and_emergency_preserve_history(agent_env):
    env = agent_env
    detail = await publish(env, await env.agents.create(env.context, env.body))
    spec = await env.agents.resolve_published(env.context, env.body.agent_code)
    releases = ReleaseService(env.agents)
    detail = await releases.state(
        env.context,
        detail.agent.agent_id,
        AgentStateInput(revision=detail.agent.revision, operation="offline", reason="计划下线"),
    )
    with pytest.raises(ServiceError):
        await env.agents.resolve_published(env.context, env.body.agent_code)
    await env.agents.check_external_boundary(env.context, spec)
    detail = await releases.state(
        env.context,
        detail.agent.agent_id,
        AgentStateInput(
            revision=detail.agent.revision, operation="emergency_stop", reason="立即止损"
        ),
    )
    with pytest.raises(ServiceError) as exc:
        await env.agents.check_external_boundary(env.context, spec)
    assert exc.value.code == "AGENT_EMERGENCY_STOP"
    assert (await env.agents.load_candidate(env.context, spec.snapshot_id)) == spec
