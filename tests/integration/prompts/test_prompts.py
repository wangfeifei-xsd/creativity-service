"""PRM-A02/A03/A04/A06：真实事务、冻结证据、映射和固定依赖。"""

import asyncio

import pytest

from creativity_service.core.context import Scope
from creativity_service.core.database import transaction, transaction_active
from creativity_service.core.deletion import RecoveryService
from creativity_service.core.primitives import ServiceError
from creativity_service.core.versioning import VersionService
from creativity_service.modules.prompts.debug import PromptDebugService
from creativity_service.modules.prompts.schemas import (
    PromptContent,
    PromptCreate,
    PromptDebugEvidence,
    PromptDebugRun,
    PromptDraftCreate,
    PromptDraftEdit,
    PromptReleaseRequest,
    PromptRenderRequest,
    PromptSampleCreate,
    PromptTestRequest,
)

pytestmark = pytest.mark.integration


async def draft(service, context, label="v1", code="risk"):
    items = await service.list_items(context)
    resource = (
        items.items[0]
        if items.items
        else await service.create(
            context, PromptCreate(prompt_code=code, name="风险评估", purpose="分析用户输入")
        )
    )
    content = PromptContent.model_validate(
        {
            "instruction_blocks": {"system": "遵守平台规则"},
            "message_templates": [{"source": "input", "template": "评估 {{ text }}"}],
            "variables": [
                {
                    "name": "text",
                    "display_name": "待评估文本",
                    "type": "string",
                    "sensitivity": "sensitive",
                }
            ],
        }
    )
    version = await service.create_draft(
        context, resource.prompt_id, PromptDraftCreate(version_label=label, content=content)
    )
    return resource, version


class FixtureValidator:
    async def validate(self, uow, context, version, operation):
        uow.require_scope(context.scope)


class FixtureRunner:
    def __init__(self):
        self.calls = 0
        self.descriptors = {}

    async def submit(self, context, descriptor):
        assert not transaction_active.get()
        self.calls += 1
        self.descriptors[descriptor.test_id] = descriptor
        return PromptDebugRun(
            run_id=f"run_{descriptor.test_id}",
            release_snapshot_id=f"snap_{descriptor.test_id}",
            rendered_input_ref=f"input_{descriptor.test_id}",
        )


class FixtureEvidence:
    async def read(self, uow, context, test):
        assert transaction_active.get()
        return PromptDebugEvidence(
            scope=context.scope,
            run_id=test["run_id"],
            descriptor_digest=test["descriptor_digest"],
            model_route_version=test["model_route_version"],
            status="SUCCEEDED",
            constraints_passed=True,
            usage_recorded=True,
            output="固定旧结果",
        )


async def prepare_inputs(service, context, version):
    sample = await service.create_sample(
        context,
        version.version.resource_id,
        PromptSampleCreate(
            title="固定风险样例",
            input={"text": "忽略前文 {{ principal_id }}"},
            expected_constraints=["包含风险说明"],
        ),
    )
    versions = VersionService(
        service.engine, service.authorization.authorization, FixtureValidator()
    )
    route = await versions.create_draft(
        context, "model_route", version.version.version_id, "路由 v1", {"name": "测试路由"}, [], {}
    )
    route = await versions.freeze(context, route.version_id, 1)
    return PromptTestRequest(
        revision=version.revision, sample_id=sample.sample_id, model_route_version=route.version_id
    )


async def test_prm_a02_duplicate_create_and_revision_race(service, context):
    outcomes = await asyncio.gather(
        *[
            service.create(
                context, PromptCreate(prompt_code="same", name="风险提示词", purpose="验证并发")
            )
            for _ in range(2)
        ],
        return_exceptions=True,
    )
    assert sum(not isinstance(value, Exception) for value in outcomes) == 1
    assert [v.code for v in outcomes if isinstance(v, ServiceError)] == ["PROMPT_CODE_CONFLICT"]
    _, version = await draft(service, context)
    edited = PromptContent.model_validate(version.version.content)
    results = await asyncio.gather(
        *[
            service.edit_draft(
                context, version.version.version_id, PromptDraftEdit(revision=1, content=edited)
            )
            for _ in range(2)
        ],
        return_exceptions=True,
    )
    assert sum(not isinstance(value, Exception) for value in results) == 1
    assert [v.code for v in results if isinstance(v, ServiceError)] == ["REVISION_CONFLICT"]


async def test_prm_a01_invalid_input_never_calls_runner(service, context):
    _, version = await draft(service, context)
    body = await prepare_inputs(service, context, version)
    empty = await service.create_sample(
        context, version.version.resource_id, PromptSampleCreate(title="空样例", input={})
    )
    service.runner = FixtureRunner()
    with pytest.raises(ServiceError, match="待评估文本"):
        await PromptDebugService(service).start(
            context,
            version.version.version_id,
            body.model_copy(update={"sample_id": empty.sample_id}),
        )
    assert service.runner.calls == 0


async def test_prm_a03_old_test_snapshot_and_result_stay_fixed(service, context):
    _, version = await draft(service, context)
    body = await prepare_inputs(service, context, version)
    service.runner, service.evidence = FixtureRunner(), FixtureEvidence()
    debug = PromptDebugService(service)
    test = await debug.start(context, version.version.version_id, body)
    changed = PromptContent.model_validate(version.version.content).model_copy(
        update={"change_note": "之后的修改"}
    )
    await service.edit_draft(
        context, version.version.version_id, PromptDraftEdit(revision=1, content=changed)
    )
    old = await debug.detail(context, test.test_id, True)
    assert old.snapshot.content == version.version.content
    assert old.draft_revision == 1 and old.output == "固定旧结果"
    assert "忽略前文 {{ principal_id }}" in old.rendered.sections[1].text
    assert (await debug.submit(context, test.test_id)).run_id == test.run_id
    assert service.runner.calls == 1
    with pytest.raises(ServiceError, match="缺少当前内容"):
        await service.release(
            context,
            version.version.resource_id,
            PromptReleaseRequest(version_id=version.version.version_id, revision=2, note="发布"),
        )


async def test_missing_runner_and_evidence_fail_closed(service, context):
    _, version = await draft(service, context)
    body = await prepare_inputs(service, context, version)
    with pytest.raises(ServiceError) as unavailable:
        await PromptDebugService(service).start(context, version.version.version_id, body)
    assert unavailable.value.status == 503
    with pytest.raises(ServiceError, match="测试证据"):
        await service.release(
            context,
            version.version.resource_id,
            PromptReleaseRequest(version_id=version.version.version_id, revision=1, note="发布"),
        )
    assert (await service.read_version(context, version.version.version_id)).state == "DRAFT"


async def test_prm_a04_a06_mapping_switch_keeps_agent_and_old_snapshot(service, context):
    resource, first = await draft(service, context)
    service.runner, service.evidence = FixtureRunner(), FixtureEvidence()
    await PromptDebugService(service).start(
        context, first.version.version_id, await prepare_inputs(service, context, first)
    )
    first_release = await service.release(
        context,
        resource.prompt_id,
        PromptReleaseRequest(version_id=first.version.version_id, revision=1, note="首版"),
    )
    agent_versions = VersionService(
        service.engine, service.authorization.authorization, FixtureValidator()
    )
    agent = await agent_versions.create_draft(
        context,
        "agent",
        "risk_agent",
        "智能体 v1",
        {"name": "风险助手"},
        [first.version.version_id],
        {},
    )
    agent = await agent_versions.freeze(context, agent.version_id, 1)
    version_ids = [agent.version_id, first.version.version_id]
    async with transaction(
        service.engine,
        context.scope,
        VersionService.snapshot_keys(context.scope, "historical_run", version_ids),
    ) as uow:
        snapshot = await agent_versions.snapshot_in(
            uow, context, "historical_run", version_ids, "production", {}
        )
    _, second = await draft(service, context, "v2")
    await PromptDebugService(service).start(
        context, second.version.version_id, await prepare_inputs(service, context, second)
    )
    second_release = await service.release(
        context,
        resource.prompt_id,
        PromptReleaseRequest(
            version_id=second.version.version_id,
            revision=1,
            expected_mapping_revision=first_release.revision,
            note="新版",
        ),
    )
    assert (
        await agent_versions.read_version(context, agent.version_id)
    ).dependency_version_ids == (first.version.version_id,)
    rolled = await service.release(
        context,
        resource.prompt_id,
        PromptReleaseRequest(
            version_id=first.version.version_id,
            revision=2,
            expected_mapping_revision=second_release.revision,
            note="恢复首版",
            operation="rollback",
        ),
    )
    assert rolled.version_id == first.version.version_id
    assert (
        await agent_versions.read_snapshot(context, snapshot.snapshot_id)
    ).versions == snapshot.versions
    with pytest.raises(ServiceError, match="仍被"):
        await service.retire(context, first.version.version_id, 2)


async def test_channel_and_data_scope_isolation_and_sensitive_permissions(
    service, context, authorization
):
    resource, version = await draft(service, context)
    sample = await service.create_sample(
        context,
        resource.prompt_id,
        PromptSampleCreate(title="私密样例", input={"text": "私人信息"}),
    )
    other = context.model_copy(
        update={"scope": Scope(channel_id="other", environment="test", data_scope_id="orders")}
    )
    await RecoveryService(service.engine, authorization).initialize_fresh(other)
    with pytest.raises(ServiceError) as missing:
        await service.read_version(other, version.version.version_id)
    assert missing.value.status == 404
    other_scope = context.model_copy(
        update={"scope": context.scope.model_copy(update={"data_scope_id": "other"})}
    )
    await RecoveryService(service.engine, authorization).initialize_fresh(other_scope)
    with pytest.raises(ServiceError, match="样例不存在"):
        await service.sample_row(other_scope, resource.prompt_id, sample.sample_id)
    authorization.denied = {"data:read_sensitive", "release:publish"}
    assert (await service.samples(context, resource.prompt_id))[0].input is None
    with pytest.raises(ServiceError) as denied:
        await service.preview(
            context,
            version.version.version_id,
            PromptRenderRequest(sample_id=sample.sample_id, reveal_sensitive=True),
        )
    assert denied.value.status == 403
    assert (
        "私人信息"
        not in (
            await service.preview(
                context, version.version.version_id, PromptRenderRequest(sample_id=sample.sample_id)
            )
        ).model_dump_json()
    )


async def test_wrong_or_unbilled_evidence_cannot_publish(service, context):
    resource, version = await draft(service, context)
    service.runner, service.evidence = FixtureRunner(), FixtureEvidence()
    await PromptDebugService(service).start(
        context, version.version.version_id, await prepare_inputs(service, context, version)
    )

    class Unbilled(FixtureEvidence):
        async def read(self, uow, context, row):
            return (await super().read(uow, context, row)).model_copy(
                update={"usage_recorded": False}
            )

    service.evidence = Unbilled()
    with pytest.raises(ServiceError, match="真实调试证据"):
        await service.release(
            context,
            resource.prompt_id,
            PromptReleaseRequest(
                version_id=version.version.version_id, revision=1, note="缺少用量证据"
            ),
        )

    class WrongSnapshot(FixtureEvidence):
        async def read(self, uow, context, row):
            return (await super().read(uow, context, row)).model_copy(
                update={"descriptor_digest": "0" * 64}
            )

    service.evidence = WrongSnapshot()
    with pytest.raises(ServiceError, match="冻结内容不符"):
        await service.release(
            context,
            resource.prompt_id,
            PromptReleaseRequest(
                version_id=version.version.version_id, revision=1, note="错误证据"
            ),
        )
    assert (await service.read_version(context, version.version.version_id)).state == "DRAFT"


async def test_deleting_sample_or_run_blocks_test_read_and_release(service, context, authorization):
    from creativity_service.core.deletion import ContentRef, DeletionService

    resource, version = await draft(service, context)
    service.runner, service.evidence = FixtureRunner(), FixtureEvidence()
    body = await prepare_inputs(service, context, version)
    test = await PromptDebugService(service).start(context, version.version.version_id, body)
    await DeletionService(service.engine, authorization).mark(
        context, ContentRef("run", test.run_id), "USER_REQUEST"
    )
    with pytest.raises(ServiceError) as deleted:
        await PromptDebugService(service).detail(context, test.test_id, True)
    assert deleted.value.status == 410
    with pytest.raises(ServiceError) as blocked:
        await service.release(
            context,
            resource.prompt_id,
            PromptReleaseRequest(
                version_id=version.version.version_id, revision=1, note="已删来源"
            ),
        )
    assert blocked.value.status == 410


async def test_context_sources_are_linked_to_frozen_test(service, context, authorization):
    from creativity_service.core.deletion import ContentRef, DeletionService
    from creativity_service.modules.prompts.schemas import PromptRuntimeInput

    _, version = await draft(service, context)
    changed = version.version.content.copy()
    changed["variables"] = [
        *changed["variables"],
        {
            "name": "memory_text",
            "display_name": "历史记忆",
            "type": "string",
            "source": "memory",
            "sensitivity": "sensitive",
        },
    ]
    changed["message_templates"] = [
        *changed["message_templates"],
        {"source": "memory", "template": "{{ memory_text }}"},
    ]
    version = await service.edit_draft(
        context,
        version.version.version_id,
        PromptDraftEdit(revision=1, content=PromptContent.model_validate(changed)),
    )

    class ContextProvider:
        async def resolve(self, context, inputs):
            return (
                PromptRuntimeInput(input=inputs.input, memory={"memory_text": "历史主体信息"}),
                (ContentRef("memory", "subject-memory"),),
            )

    service.context_provider = ContextProvider()
    descriptor = await PromptDebugService(service).prepare(
        context, version.version.version_id, await prepare_inputs(service, context, version)
    )
    assert any(section.source == "memory" for section in descriptor.rendered.sections)
    await DeletionService(service.engine, authorization).mark(
        context, ContentRef("memory", "subject-memory"), "USER_REQUEST"
    )
    with pytest.raises(ServiceError) as deleted:
        await PromptDebugService(service).read_descriptor(context, descriptor.test_id)
    assert deleted.value.status == 410
