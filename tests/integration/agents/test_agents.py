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
    assert (
        sum(isinstance(r, ServiceError) and r.code == "REVISION_CONFLICT" for r in releases) == 1
    ), [str(r) for r in releases]
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
    config = env.definition.model_copy(
        update={"limits": env.definition.limits.model_copy(update={"max_tool_calls": 0})}
    )
    detail = await env.agents.create(
        env.context, env.body.model_copy(update={"definition": config})
    )
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
    resolver = AgentRunResolver(env.agents)
    resolved = resolver.definition(candidate)
    assert resolved.policy.max_tool_calls == 0
    for changed in (
        resolved.model_copy(
            update={"policy": resolved.policy.model_copy(update={"max_model_calls": 100})}
        ),
        resolved.model_copy(update={"input_schema": {"type": "object"}}),
    ):
        with pytest.raises(ServiceError) as exc:
            async with transaction(
                env.engine, env.context.scope, resolver.keys(env.context, changed)
            ) as uow:
                await resolver.validate_in(uow, env.context, changed)
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
    other = await provision(env, "other", "club")
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


@pytest.mark.parametrize("agent_env", ["prod"], indirect=True)
async def test_prod_http_rechecks_evidence_and_draft_changes(agent_env):
    env = agent_env
    detail = await env.agents.create(env.context, env.body)
    draft = detail.versions[0]
    url = f"/admin/v1/agents/{detail.agent.agent_id}/releases"
    body = {
        "version_id": draft.version_id,
        "revision": 1,
        "environment": "prod",
        "note": "评测门禁验证",
        "evaluation_refs": ["report_fixture"],
    }
    response = await env.client.post(url, json=body)
    assert response.status_code == 422 and response.json()["error"]["code"] == "EVALUATION_REQUIRED"
    candidate = await env.agents.freeze_candidate(env.context, draft.version_id, 1, "evaluation")

    class Gate:
        def keys(self, context, refs):
            from creativity_service.core.locking import ResourceKey

            return [ResourceKey(context.scope.channel_id, "evaluation-evidence", tuple(refs))]

        async def read(self, uow, context, refs):
            for key in self.keys(context, refs):
                uow.require_lock(key)
            return self.evidence

    gate = Gate()
    gate.evidence = EvaluationEvidence(
        channel_id=env.context.scope.channel_id,
        agent_id=detail.agent.agent_id,
        environment="prod",
        content_digest=candidate.content_digest,
        dependencies_digest="f" * 64,
        report_digest=digest(["report-fixture"]),
        report_ids=("report_fixture",),
        passed=True,
        expires_at=utcnow() + timedelta(hours=1),
    )
    env.agents.evaluation = gate
    response = await env.client.post(url, json=body)
    assert response.status_code == 422 and response.json()["error"]["code"] == "EVALUATION_STALE"
    gate.evidence = gate.evidence.model_copy(
        update={"dependencies_digest": candidate.dependencies_digest}
    )
    response = await env.client.post(url, json=body)
    assert response.status_code == 200, response.text
    result = response.json()
    assert result["release_version_id"] != draft.version_id
    assert (
        await env.agents.resolve_published(env.context, env.body.agent_code)
    ).candidate_digest == candidate.candidate_digest
    await env.agents.edit_version(
        env.context,
        draft.version_id,
        AgentVersionEdit(
            revision=1,
            definition=env.definition.model_copy(
                update={"limits": env.definition.limits.model_copy(update={"max_model_rounds": 2})}
            ),
        ),
    )
    response = await env.client.post(
        url, json={**body, "revision": 2, "expected_mapping_revision": result["release_revision"]}
    )
    assert response.status_code == 422 and response.json()["error"]["code"] == "EVALUATION_STALE"
    unchanged = await env.agents.detail(env.context, detail.agent.agent_id)
    assert (
        unchanged.release_version_id == result["release_version_id"]
        and len(unchanged.releases) == 1
    )


async def with_tool(env, required_scopes=("run:create",)):
    from creativity_service.modules.tools.schemas import (
        ToolCreate,
        ToolDefinition,
        ToolVersionCreate,
    )

    tool = await env.tools.management.create(
        env.context,
        ToolCreate(
            tool_code="sum",
            name="精确求和",
            description="计算合计",
            owner="负责人",
            source_type="builtin",
        ),
    )
    tool_definition = ToolDefinition(
        input_schema={
            "type": "object",
            "properties": {"values": {"type": "array", "items": {"type": "string"}}},
            "required": ["values"],
            "additionalProperties": False,
        },
        output_schema={
            "type": "object",
            "properties": {"sum": {"type": "string"}},
            "required": ["sum"],
            "additionalProperties": False,
        },
        model_fields_allowed=("values",),
        binding={"adapter_key": "decimal_sum", "implementation_version": "1"},
        effect_type="READ_ONLY",
        required_scopes=required_scopes,
        environments=("test",),
        subject_requirements={"required": False},
    )
    version = await env.tools.management.create_version(
        env.context,
        tool.tool_id,
        ToolVersionCreate(version_label="初版", definition=tool_definition),
    )
    frozen = await env.tools.management.freeze(
        env.context, version.version.version_id, version.revision
    )
    definition = env.definition.model_copy(
        update={
            "bindings": env.definition.bindings.model_copy(
                update={"tool_versions": (frozen.version.version_id,)}
            )
        }
    )
    detail = await env.agents.create(
        env.context, env.body.model_copy(update={"definition": definition})
    )
    return tool, detail


async def test_agt_a01_tool_scopes_cannot_expand_caller_authorization(agent_env):
    env = agent_env
    _, detail = await with_tool(env, ("run:create", "data:read_sensitive"))
    result = await env.agents.validate(
        env.context,
        detail.versions[0].version_id,
        AgentValidateInput(revision=1, purpose="production"),
    )
    assert not result.valid
    assert any(i.code == "FORBIDDEN" for c in result.checks for i in c.issues)
    with pytest.raises(ServiceError) as exc:
        await publish(env, detail)
    assert exc.value.code == "FORBIDDEN"


async def test_agt_a05_rollback_rechecks_disabled_tool(agent_env):
    env = agent_env
    tool, detail = await with_tool(env)
    first = await publish(env, detail)
    published = next(v for v in first.versions if v.status.value == "PUBLISHED")
    await env.tools.management.disable(env.context, tool.tool_id, tool.revision)
    with pytest.raises(ServiceError) as exc:
        await publish(
            env,
            first,
            version_id=published.version_id,
            revision=published.revision,
            operation="rollback",
        )
    assert exc.value.code == "DEPENDENCY_INVALID"
    assert (
        await env.agents.detail(env.context, detail.agent.agent_id)
    ).release_revision == first.release_revision


async def test_actual_admission_keeps_queued_run_and_new_request_gets_new_version(agent_env):
    from creativity_service.core.primitives import RunInput
    from creativity_service.modules.runs.assembly import build_run_service
    from creativity_service.modules.runs.repositories import one
    from creativity_service.modules.usage.services import UsageService

    env = agent_env
    first = await publish(env, await env.agents.create(env.context, env.body))
    runs = build_run_service(
        env.engine,
        env.iam,
        VersionService(env.engine, env.iam.authorization),
        env.agents.budgets,
        UsageService(env.engine, env.agents.budgets),
        resolver=AgentRunResolver(env.agents),
    )
    request = RunInput(agent_code=env.body.agent_code, input={"request": "整理信息"})
    old_run = await runs.admit_run(env.context, request, "first")
    assert old_run.state == "QUEUED"
    draft = next(v for v in first.versions if v.status.value == "DRAFT")
    await env.agents.edit_version(
        env.context,
        draft.version_id,
        AgentVersionEdit(
            revision=draft.revision,
            definition=env.definition.model_copy(
                update={"limits": env.definition.limits.model_copy(update={"max_model_rounds": 2})}
            ),
        ),
    )
    latest = await publish(env, await env.agents.detail(env.context, first.agent.agent_id))
    new_run = await runs.admit_run(env.context, request, "second")
    async with env.engine.connect() as connection:
        old = await one(connection, "runs", env.context.scope.channel_id, id=old_run.run_id)
        new = await one(connection, "runs", env.context.scope.channel_id, id=new_run.run_id)
        old_snapshot = await repository("release_snapshots", env.context.scope).get(
            connection, old["release_snapshot_id"]
        )
        new_snapshot = await repository("release_snapshots", env.context.scope).get(
            connection, new["release_snapshot_id"]
        )
    assert old["conversation_id"] is None and new["conversation_id"] is None
    assert old["agent_version_id"] == first.release_version_id
    assert new["agent_version_id"] == latest.release_version_id
    assert old_snapshot["versions"][0]["content"]["limits"]["max_model_rounds"] == 6
    assert new_snapshot["versions"][0]["content"]["limits"]["max_model_rounds"] == 2
    old_candidate = await env.agents.load_candidate(
        env.context, old["execution_policy"]["frozen_spec_id"]
    )
    new_candidate = await env.agents.load_candidate(
        env.context, new["execution_policy"]["frozen_spec_id"]
    )
    assert old_candidate.source_version_id == first.release_version_id
    assert new_candidate.source_version_id == latest.release_version_id
    assert (await runs.admit_run(env.context, request, "first")).run_id == old_run.run_id


async def test_candidate_cleanup_follows_deleted_sources_and_preserves_digests(agent_env):
    from creativity_service.core.deletion import ContentRef, DeletionService

    env = agent_env
    detail = await env.agents.create(env.context, env.body)
    version_id = detail.versions[0].version_id
    candidate = await env.agents.freeze_candidate(env.context, version_id, 1)
    ref = ContentRef("agent_candidate", candidate.snapshot_id)
    with pytest.raises(ServiceError) as exc:
        await env.cleanup.clean(env.context, ref)
    assert exc.value.code == "DELETION_MARKER_REQUIRED"
    async with env.engine.connect() as connection:
        links = await repository("source_links", env.context.scope).find(
            connection, derived_type=ref.resource_type, derived_id=ref.resource_id
        )
    assert {link["source_id"] for link in links} == {
        detail.agent.agent_id,
        *[v.version_id for v in candidate.versions],
    }

    class SourceAuthorization:
        async def require(self, context, action, resource_id):
            # 25 的有类型来源授权入口；依然使用当前真实 IAM 授权。
            await env.iam.authorization.boundary(context, action, "version", resource_id)

    await DeletionService(env.engine, SourceAuthorization()).mark(
        env.context, ContentRef("version", version_id), "USER_REQUEST"
    )
    with pytest.raises(ServiceError) as exc:
        await env.agents.load_candidate(env.context, candidate.snapshot_id)
    assert exc.value.code == "CONTENT_DELETED"
    assert not (await env.agents.detail(env.context, detail.agent.agent_id)).versions
    await env.cleanup.clean(env.context, ref)
    await env.cleanup.clean(env.context, ref)
    async with env.engine.connect() as connection:
        row = await repository("agent_candidates", env.context.scope).get(
            connection, candidate.snapshot_id
        )
    assert row["spec"] == {} and row["candidate_digest"] == candidate.candidate_digest


async def test_draft_dependency_revision_is_frozen_and_requires_publication(agent_env):
    from creativity_service.modules.prompts.schemas import (
        PromptContent,
        PromptDraftCreate,
        PromptDraftEdit,
    )

    env = agent_env
    async with env.engine.connect() as connection:
        prompt = await repository("resource_versions", env.context.scope).get(
            connection, env.definition.bindings.prompt_version
        )
    draft = await env.prompts.create_draft(
        env.context,
        prompt["resource_id"],
        PromptDraftCreate(
            version_label="待评测草稿", content=PromptContent(change_note="原始内容")
        ),
    )
    definition = env.definition.model_copy(
        update={
            "bindings": env.definition.bindings.model_copy(
                update={"prompt_version": draft.version.version_id}
            )
        }
    )
    detail = await env.agents.create(
        env.context, env.body.model_copy(update={"definition": definition})
    )
    candidate = await env.agents.freeze_candidate(
        env.context, detail.versions[0].version_id, 1, "evaluation"
    )
    await env.prompts.edit_draft(
        env.context,
        draft.version.version_id,
        PromptDraftEdit(revision=draft.revision, content=PromptContent(change_note="变更后的内容")),
    )
    new = await env.agents.freeze_candidate(
        env.context, detail.versions[0].version_id, 1, "evaluation"
    )
    assert new.dependencies_digest != candidate.dependencies_digest
    original = next(v for v in candidate.versions if v.resource_type == "prompt")
    assert (
        original.draft_revision == draft.revision and original.content["change_note"] == "原始内容"
    )
    with pytest.raises(ServiceError) as exc:
        await publish(env, detail)
    assert exc.value.code == "DEPENDENCY_INVALID"


async def test_amount_budget_requires_prices_and_scope_overrides_fail(agent_env):
    from creativity_service.core.primitives import Money

    env = agent_env
    definition = env.definition.model_copy(
        update={
            "limits": env.definition.limits.model_copy(
                update={"cost_limit": Money(amount="1", currency="CNY")}
            )
        }
    )
    detail = await env.agents.create(
        env.context, env.body.model_copy(update={"definition": definition})
    )
    validation = await env.agents.validate(
        env.context, detail.versions[0].version_id, AgentValidateInput(revision=1)
    )
    assert any(i.code == "BUDGET_NOT_EXECUTABLE" for c in validation.checks for i in c.issues)
    with pytest.raises(ServiceError) as exc:
        await publish(env, detail, environment="prod")
    assert exc.value.code == "FORBIDDEN"
    response = await env.client.post(
        f"/admin/v1/agent-versions/{detail.versions[0].version_id}/tests",
        json={
            "revision": 1,
            "input": {"request": "查询"},
            "idempotency_key": "debug",
            "snapshot": {"override": True},
        },
    )
    assert response.status_code == 422
