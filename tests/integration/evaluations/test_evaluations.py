"""固定比较、版本变更、关键门禁、取消及删除传播验收。"""

import pytest

from creativity_service.core.context import TaskEnvelope
from creativity_service.core.primitives import utcnow
from creativity_service.modules.evaluations.repositories import repository
from creativity_service.modules.evaluations.schemas import (
    Assertion,
    CaseInput,
    DatasetCreate,
    DatasetVersionInput,
    EvaluationCreate,
)
from creativity_service.workers.executor import execute_message

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


async def prepare(env, count=2, token_limit=1000000):
    detail = await env.agents.create(env.context, env.body)
    dataset = await env.evaluations.create_dataset(
        env.context,
        DatasetCreate(
            name="通用结构输出",
            scenario="结构契约",
            owner="技术审阅者",
            applicability="固定模型响应与通用断言框架",
        ),
    )
    cases = [
        CaseInput(
            case_key=f"case_{i}",
            title=f"样本 {i + 1}",
            input={"request": "验证输入"},
            assertions=[
                {"kind": "equal", "name": "答复内容", "path": "data.answer", "expected": "验证完成"}
            ],
            label_source="人工审阅夹具",
            human_label={"decision": "approved", "reason": "固定预期已核对"},
        )
        for i in range(count)
    ]
    dataset = await env.evaluations.create_version(
        env.context,
        dataset.dataset_id,
        DatasetVersionInput(
            revision=dataset.revision, version_label="样本第一版", cases=cases, captured_at=utcnow()
        ),
    )
    version = detail.versions[0]
    task = await env.evaluations.create(
        env.context,
        EvaluationCreate(
            name="发布前回归",
            dataset_version_id=dataset.current_version_id,
            candidates=[{"version_id": version.version_id, "revision": version.revision}],
            release_target=True,
            concurrency=1,
            budget={"max_runs": count + 3, "token_limit": token_limit},
        ),
    )
    return detail, dataset, task


async def advance(env, task):
    await env.evaluations.tick(env.context, task.evaluation_id)
    async with env.engine.connect() as connection:
        rows = await repository("evaluation_results", env.context.scope).find(
            connection, evaluation_id=task.evaluation_id
        )
    for row in rows:
        if row["state"] == "RUNNING":
            await execute_message(
                env.runs,
                TaskEnvelope(channel_id=env.context.scope.channel_id, run_id=row["run_id"]),
                "evaluation-test-worker",
                env.runtime,
            )
    await env.evaluations.tick(env.context, task.evaluation_id)


async def test_complete_report(evaluation_env):
    env = evaluation_env
    detail, dataset, task = await prepare(env)
    for _ in range(4):
        await advance(env, task)
    report = await env.evaluations.comparison(env.context, task.evaluation_id)
    assert report.complete
    assert report.candidates[0]["counts"]["PASSED"] == 2
    assert report.cost["run_count"] == 2
    assert not report.candidates[0]["release_passed"]


async def finish(env, task):
    for _ in range(6):
        await advance(env, task)
        current = await env.evaluations.detail(env.context, task.evaluation_id)
        if current.state.value == "COMPLETED":
            return current
    raise AssertionError("评测未在预期轮数内完成")


async def approve(env, task):
    from creativity_service.modules.evaluations.schemas import EvaluationReview

    current = await env.evaluations.detail(env.context, task.evaluation_id)
    return await env.evaluations.review(
        env.context,
        task.evaluation_id,
        EvaluationReview(
            revision=current.revision,
            label={"decision": "approved", "reason": "范围、固定标签和报告已审阅"},
        ),
    )


async def test_release_content_change_and_initial_baseline(evaluation_env):
    from creativity_service.modules.agents.schemas import AgentValidateInput, AgentVersionEdit
    from tests.integration.agents.test_agents import publish

    env = evaluation_env
    detail, _, task = await prepare(env)
    await finish(env, task)
    await approve(env, task)
    version = detail.versions[0]
    body = AgentValidateInput(
        purpose="production", revision=version.revision, evaluation_refs=(task.evaluation_id,)
    )
    response = await env.client.post(
        f"/admin/v1/agent-versions/{version.version_id}/release-check",
        json=body.model_dump(mode="json"),
    )
    assert response.status_code == 200 and response.json()["valid"], response.text
    published = await publish(env, detail, evaluation_refs=(task.evaluation_id,))
    assert published.release_version_id != version.version_id
    definition = version.definition.model_copy(
        update={"limits": version.definition.limits.model_copy(update={"max_model_rounds": 4})}
    )
    changed = await env.agents.edit_version(
        env.context,
        version.version_id,
        AgentVersionEdit(revision=version.revision, definition=definition),
    )
    checked = await env.agents.validate(
        env.context, version.version_id, body.model_copy(update={"revision": changed.revision})
    )
    assert not checked.valid


async def test_cancel_counts_unexecuted_and_preserves_usage(evaluation_env):
    from creativity_service.modules.evaluations.schemas import EvaluationControl

    env = evaluation_env
    _, _, task = await prepare(env, 3)
    await advance(env, task)
    current = await env.evaluations.detail(env.context, task.evaluation_id)
    await env.evaluations.control(
        env.context,
        task.evaluation_id,
        EvaluationControl(revision=current.revision, operation="cancel"),
    )
    await env.evaluations.tick(env.context, task.evaluation_id)
    report = await env.evaluations.comparison(env.context, task.evaluation_id)
    assert not report.complete
    assert report.candidates[0]["counts"]["PASSED"] == 1
    assert report.candidates[0]["pass_rate"] == pytest.approx(1 / 3)
    assert report.cost["known_tokens"] == 30
    assert sum(report.candidates[0]["counts"][s] for s in ("UNEXECUTED", "CANCELLED")) == 2


async def test_pause_duplicate_dispatch_and_single_rerun(evaluation_env):
    import asyncio

    from creativity_service.modules.evaluations.schemas import EvaluationControl, RerunInput

    env = evaluation_env
    _, _, task = await prepare(env)
    await asyncio.gather(*(env.evaluations.tick(env.context, task.evaluation_id) for _ in range(2)))
    async with env.engine.connect() as connection:
        entries = await repository("evaluation_results", env.context.scope).find(
            connection, evaluation_id=task.evaluation_id
        )
    assert sum(bool(r["run_id"]) for r in entries) == 1
    current = await env.evaluations.detail(env.context, task.evaluation_id)
    await env.evaluations.control(
        env.context,
        task.evaluation_id,
        EvaluationControl(revision=current.revision, operation="pause"),
    )
    await advance(env, task)
    paused = await env.evaluations.comparison(env.context, task.evaluation_id)
    assert paused.cost["run_count"] == 1 and paused.candidates[0]["counts"]["PENDING"] == 1
    current = await env.evaluations.detail(env.context, task.evaluation_id)
    await env.evaluations.control(
        env.context,
        task.evaluation_id,
        EvaluationControl(revision=current.revision, operation="resume"),
    )
    current = await finish(env, task)
    report = await env.evaluations.comparison(env.context, task.evaluation_id)
    await env.evaluations.rerun(
        env.context, report.results[0]["result_id"], RerunInput(revision=current.revision)
    )
    await finish(env, task)
    report = await env.evaluations.comparison(env.context, task.evaluation_id)
    assert report.cost["run_count"] == 3 and len(report.results) == 2
    assert max(r["attempt_number"] for r in report.results) == 2


async def test_critical_failure_blocks_even_at_zero_quality_threshold(evaluation_env):
    from creativity_service.modules.agents.schemas import AgentValidateInput
    from creativity_service.modules.evaluations.schemas import RerunInput

    env = evaluation_env
    detail, dataset, first = await prepare(env, 1)
    current = dataset.versions[-1]
    case = current.cases[0].payload.model_copy(
        update={
            "assertions": [
                Assertion(
                    kind="forbidden",
                    name="敏感信息不得泄露",
                    path="data.answer",
                    expected=["验证完成"],
                    category="secret_leak",
                )
            ]
        }
    )
    dataset = await env.evaluations.create_version(
        env.context,
        dataset.dataset_id,
        DatasetVersionInput(
            revision=dataset.revision, version_label="敏感样本", cases=[case], captured_at=utcnow()
        ),
    )
    version = detail.versions[0]
    task = await env.evaluations.create(
        env.context,
        EvaluationCreate(
            name="关键失败检查",
            dataset_version_id=dataset.current_version_id,
            candidates=[{"version_id": version.version_id, "revision": version.revision}],
            release_target=True,
            minimum_pass_rate=0,
            budget={"max_runs": 2, "token_limit": 1000000},
        ),
    )
    await finish(env, task)
    await approve(env, task)
    report = await env.evaluations.comparison(env.context, task.evaluation_id)
    assert report.candidates[0]["critical_failures"] == 1
    checked = await env.agents.validate(
        env.context,
        version.version_id,
        AgentValidateInput(
            purpose="production", revision=version.revision, evaluation_refs=(task.evaluation_id,)
        ),
    )
    assert not checked.valid
    current_task = await env.evaluations.detail(env.context, task.evaluation_id)
    await env.evaluations.rerun(
        env.context, report.results[0]["result_id"], RerunInput(revision=current_task.revision)
    )
    env.adapter.responses = [
        {
            "business_status": "COMPLETED",
            "schema_version": "1.0",
            "data": {"answer": "安全答复"},
            "warnings": [],
            "evidence_refs": [],
        }
    ]
    await finish(env, task)
    await approve(env, task)
    report = await env.evaluations.comparison(env.context, task.evaluation_id)
    assert report.results[0]["state"] == "PASSED"
    assert report.candidates[0]["critical_failures"] == 1
    assert not report.candidates[0]["release_passed"]
    assert report.cost["run_count"] == 2


async def test_source_deletion_hides_case_report_and_blocks_release(evaluation_env):
    from creativity_service.core.deletion import ContentRef, DeletionService
    from creativity_service.core.primitives import ServiceError
    from creativity_service.modules.agents.schemas import AgentValidateInput

    env = evaluation_env
    detail, dataset, task = await prepare(env, 1)
    await finish(env, task)
    await approve(env, task)
    ref = ContentRef("evaluation_case", dataset.versions[-1].cases[0].case_id)
    await DeletionService(env.engine, env.iam.authorization).mark(env.context, ref, "个人删除")
    report = await env.evaluations.comparison(env.context, task.evaluation_id)
    assert not report.reproducible and report.results[0]["judgment"] is None
    assert report.results[0]["title"] == "来源已删除的样本"
    data = await env.evaluations.dataset(env.context, dataset.dataset_id)
    assert data.versions[-1].cases[0].payload is None
    await env.cleanup.clean(env.context, ref)
    async with env.engine.connect() as connection:
        assert (
            await repository("evaluation_cases", env.context.scope).get(connection, ref.resource_id)
        )["payload"] is None
    version = detail.versions[0]
    try:
        checked = await env.agents.validate(
            env.context,
            version.version_id,
            AgentValidateInput(
                purpose="production",
                revision=version.revision,
                evaluation_refs=(task.evaluation_id,),
            ),
        )
    except ServiceError as exc:
        assert exc.code == "CONTENT_DELETED"
    else:
        assert not checked.valid


async def test_import_atomicity_and_label_versioning(evaluation_env):
    from creativity_service.core.primitives import ServiceError
    from creativity_service.modules.evaluations.schemas import CaseEdit, ImportInput

    env = evaluation_env
    _, dataset, _ = await prepare(env, 1)
    case = dataset.versions[-1].cases[0]
    body = ImportInput(
        format="jsonl",
        content=case.payload.model_dump_json() + "\n{bad json}",
        revision=dataset.revision,
        version_label="导入待修正",
        captured_at=utcnow(),
    )
    preview = await env.evaluations.import_cases(env.context, dataset.dataset_id, body)
    assert preview.valid_count == 1 and preview.rows[1].row_number == 2 and preview.error_count == 1
    with pytest.raises(ServiceError, match="修正"):
        await env.evaluations.import_cases(
            env.context,
            dataset.dataset_id,
            body.model_copy(update={"commit": True, "preview_digest": preview.preview_digest}),
        )
    before = dataset.current_version_id
    changed = await env.evaluations.edit_case(
        env.context,
        case.case_id,
        CaseEdit(
            revision=dataset.revision,
            version_label="复核标签",
            case=case.payload.model_copy(update={"labels": ["边界"]}),
        ),
    )
    assert changed.current_version_id != before and len(changed.versions) == 2
    assert changed.versions[0].cases[0].payload.labels == []
    valid = body.model_copy(
        update={
            "content": case.payload.model_dump_json(),
            "revision": changed.revision,
            "version_label": "已修正导入",
        }
    )
    preview = await env.evaluations.import_cases(env.context, dataset.dataset_id, valid)
    committed = await env.evaluations.import_cases(
        env.context,
        dataset.dataset_id,
        valid.model_copy(update={"commit": True, "preview_digest": preview.preview_digest}),
    )
    latest = await env.evaluations.dataset(env.context, dataset.dataset_id)
    assert committed.version_id == latest.current_version_id and len(latest.versions) == 3
    assert latest.versions[-1].cases[0].payload == case.payload


async def test_same_data_baseline_comparison(evaluation_env):
    from creativity_service.core.deletion import ContentRef, DeletionService
    from creativity_service.modules.agents.schemas import AgentValidateInput, AgentVersionCreate
    from tests.integration.agents.test_agents import publish

    env = evaluation_env
    detail, dataset, baseline = await prepare(env, 1)
    await finish(env, baseline)
    await approve(env, baseline)
    await publish(env, detail, evaluation_refs=(baseline.evaluation_id,))
    candidate = await env.agents.create_version(
        env.context,
        detail.agent.agent_id,
        AgentVersionCreate(base_version_id=detail.versions[0].version_id, version_label="候选二"),
    )
    task = await env.evaluations.create(
        env.context,
        EvaluationCreate(
            name="同条件对比",
            dataset_version_id=dataset.current_version_id,
            candidates=[{"version_id": candidate.version_id, "revision": candidate.revision}],
            baseline_evaluation_id=baseline.evaluation_id,
            baseline_candidate_version_id=detail.versions[0].version_id,
            release_target=True,
            budget={"max_runs": 2, "token_limit": 1000000},
        ),
    )
    await finish(env, task)
    await approve(env, task)
    report = await env.evaluations.comparison(env.context, task.evaluation_id)
    assert report.baseline["comparable"]
    assert report.candidates[0]["regression"] == 0
    assert report.candidates[0]["dependencies"]
    assert report.candidates[0]["release_passed"]
    checked = await env.agents.validate(
        env.context,
        candidate.version_id,
        AgentValidateInput(
            purpose="production", revision=candidate.revision, evaluation_refs=(task.evaluation_id,)
        ),
    )
    assert checked.valid
    await DeletionService(env.engine, env.iam.authorization).mark(
        env.context, ContentRef("evaluation", baseline.evaluation_id), "删除历史基线来源"
    )
    report = await env.evaluations.comparison(env.context, task.evaluation_id)
    assert not report.baseline["comparable"]
    assert not report.candidates[0]["release_passed"]


@pytest.mark.parametrize("kind", ["text_items", "numeric_summary"])
async def test_two_shapes_full_case_matrix(evaluation_env, kind):
    import asyncio
    import json
    import os
    from pathlib import Path

    from examples.evaluations.prepare import bundle

    env = evaluation_env
    body, cases, responses = bundle(kind)
    body = body.model_copy(
        update={
            "definition": body.definition.model_copy(update={"bindings": env.definition.bindings})
        }
    )
    detail = await env.agents.create(env.context, body)
    version = detail.versions[0]
    dataset = await env.evaluations.create_dataset(
        env.context,
        DatasetCreate(
            name=body.name + "验收样本",
            scenario="通用框架",
            owner="技术审阅",
            applicability="结构、边界、缺失、权限、工具变化和故障",
        ),
    )
    dataset = await env.evaluations.create_version(
        env.context,
        dataset.dataset_id,
        DatasetVersionInput(
            revision=dataset.revision,
            version_label="固定夹具第一版",
            cases=cases,
            captured_at=utcnow(),
        ),
    )
    task = await env.evaluations.create(
        env.context,
        EvaluationCreate(
            name=body.name + "全量验证",
            dataset_version_id=dataset.current_version_id,
            candidates=[{"version_id": version.version_id, "revision": version.revision}],
            release_target=True,
            concurrency=1,
            minimum_pass_rate=0,
            budget={"max_runs": 12, "token_limit": 1000000},
        ),
    )
    for _ in range(10):
        await env.evaluations.tick(env.context, task.evaluation_id)
        async with env.engine.connect() as connection:
            rows = await repository("evaluation_results", env.context.scope).find(
                connection, evaluation_id=task.evaluation_id
            )
            samples = {
                r["id"]: r
                for r in await repository("evaluation_cases", env.context.scope).find(
                    connection, dataset_id=dataset.dataset_id
                )
            }
        for row in rows:
            if row["state"] != "RUNNING":
                continue
            key = samples[row["case_id"]]["case_key"]
            if key == "fault":
                env.adapter.failures = ["MODEL_UNAVAILABLE"] * 5
            else:
                # 用例调度顺序由标识决定，故障用例剩余重试不能污染下一条固定响应。
                env.adapter.failures = []
                env.adapter.responses = [responses[key]]
            await execute_message(
                env.runs,
                TaskEnvelope(channel_id=env.context.scope.channel_id, run_id=row["run_id"]),
                "matrix-worker",
                env.runtime,
            )
        current = await env.evaluations.detail(env.context, task.evaluation_id)
        if current.state.value == "COMPLETED":
            break
    report = await env.evaluations.comparison(env.context, task.evaluation_id)
    assert report.complete
    assert report.candidates[0]["total"] == 6
    assert report.candidates[0]["counts"]["PASSED"] == 2, report.model_dump_json()
    assert report.candidates[0]["counts"]["INVALID"] == 1
    assert report.candidates[0]["critical_failures"] >= 1
    assert not report.candidates[0]["release_passed"]
    if root := os.getenv("CREATIVITY_EVALUATION_EVIDENCE_DIR"):
        destination = Path(root)
        await asyncio.to_thread(destination.mkdir, parents=True, exist_ok=True)
        (destination / f"{kind}.report.json").write_text(
            json.dumps(
                {
                    "scope": env.context.scope.model_dump(),
                    "evidence": "fixture",
                    "label_source": "技术审阅固定预期",
                    "report": report.model_dump(mode="json"),
                },
                ensure_ascii=False,
                indent=2,
            )
            + "\n"
        )


async def test_fixture_uses_fixed_result_and_rechecks_current_permission(evaluation_env):
    from datetime import timedelta

    from creativity_service.core.database import Repository
    from creativity_service.core.primitives import digest
    from creativity_service.modules.agents.schemas import AgentEdge, AgentStep, InputSource
    from creativity_service.modules.resources.schemas import ResourceMutation
    from creativity_service.modules.resources.services import ResourceManagement
    from creativity_service.modules.tools.schemas import (
        ToolCreate,
        ToolDefinition,
        ToolVersionCreate,
    )
    from creativity_service.modules.tools.tables import metadata as tool_metadata

    env = evaluation_env
    tool = await env.tools.management.create(
        env.context,
        ToolCreate(
            tool_code="fixture_sum",
            name="通用数值工具",
            description="夹具授权验证",
            owner="技术测试",
            source_type="builtin",
        ),
    )
    contract = ToolDefinition(
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
        environments=("prod",),
        subject_requirements={"required": False},
    )
    tool_version = (
        await env.tools.management.create_version(
            env.context,
            tool.tool_id,
            ToolVersionCreate(version_label="工具草稿", definition=contract),
        )
    ).version
    # 当前依赖只允许已发布资源，夹具工具也必须走同一发布入口。
    manager = ResourceManagement(
        env.engine, env.iam.authorization, {"tool": env.tools.management.versions.validator}
    )
    resource = (await manager.summaries(env.context, "tool", [tool.tool_id]))[0]
    await manager.mutate(
        env.context,
        "tool",
        tool.tool_id,
        "publish",
        ResourceMutation(
            revision=resource.revision, configuration_revision=resource.configuration_revision
        ),
    )
    input_schema = {
        "type": "object",
        "properties": {
            "request": {"type": "string", "minLength": 1},
            "values": {"type": "array", "items": {"type": "string"}},
        },
        "required": ["request", "values"],
        "additionalProperties": False,
    }
    definition = env.definition.model_copy(
        update={
            "workflow_type": "template",
            "entrypoint": "workflow.v1",
            "input_schema": input_schema,
            "start_step": "query",
            "steps": (
                AgentStep(
                    key="query",
                    name="查询固定数据",
                    kind="tool",
                    dependency=tool_version.version_id,
                    inputs={"values": InputSource(source="input", path="values")},
                    input_schema=contract.input_schema,
                    output_schema=contract.output_schema,
                ),
                *env.definition.steps,
            ),
            "edges": (AgentEdge(source="query", target="answer"), *env.definition.edges),
            "bindings": env.definition.bindings.model_copy(
                update={"tool_versions": (tool_version.version_id,)}
            ),
        }
    )
    detail = await env.agents.create(
        env.context, env.body.model_copy(update={"definition": definition})
    )
    dataset = await env.evaluations.create_dataset(
        env.context,
        DatasetCreate(
            name="固定工具数据",
            scenario="工具契约",
            owner="技术审阅",
            applicability="权限、故障与固定时间",
        ),
    )
    sample = CaseInput(
        case_key="fixed",
        title="历史工具夹具",
        input={"request": "整理工具返回", "values": ["1", "2"]},
        label_source="已授权工具契约夹具",
        assertions=[
            {"kind": "equal", "name": "输出内容", "path": "data.answer", "expected": "验证完成"}
        ],
        fixture=[
            {
                "tool_version_id": tool_version.version_id,
                "arguments": {"values": ["1", "2"]},
                "data": {"sum": "99"},
                "source_version": "历史数据一",
                "observed_at": utcnow() - timedelta(days=7),
            }
        ],
    )
    dataset = await env.evaluations.create_version(
        env.context,
        dataset.dataset_id,
        DatasetVersionInput(
            revision=dataset.revision,
            version_label="固定第一版",
            cases=[sample],
            captured_at=utcnow(),
        ),
    )
    version = detail.versions[0]
    request = EvaluationCreate(
        name="固定数据授权检查",
        dataset_version_id=dataset.current_version_id,
        candidates=[{"version_id": version.version_id, "revision": version.revision}],
        budget={"max_runs": 3, "token_limit": 1000000},
    )
    task = await env.evaluations.create(env.context, request)
    await finish(env, task)
    report = await env.evaluations.comparison(env.context, task.evaluation_id)
    assert report.candidates[0]["counts"]["PASSED"] == 1, report.model_dump_json()
    async with env.engine.connect() as connection:
        calls = await Repository(tool_metadata.tables["tool_calls"], env.context.scope).find(
            connection, run_id=report.results[0]["run_id"]
        )
    assert calls[0]["source_request_id"].startswith("fixture_")
    assert calls[0]["result_summary"]["data_digest"] == digest({"sum": "99"})
    next_task = await env.evaluations.create(
        env.context, request.model_copy(update={"name": "撤权后夹具检查"})
    )
    await env.tools.management.disable(env.context, tool.tool_id, tool.revision)
    await finish(env, next_task)
    blocked = await env.evaluations.comparison(env.context, next_task.evaluation_id)
    assert blocked.candidates[0]["counts"]["PASSED"] == 0


async def test_reviewed_run_source_redacts_and_propagates_subject_deletion(evaluation_env):
    from creativity_service.core.context import Scope
    from creativity_service.core.deletion import ContentRef, DeletionService, RecoveryService
    from creativity_service.core.primitives import ServiceError
    from creativity_service.modules.agents.schemas import AgentTestInput
    from creativity_service.modules.evaluations.schemas import RunCaseInput

    env = evaluation_env
    detail, dataset, _ = await prepare(env, 1)
    source_context = env.context.model_copy(
        update={
            "scope": Scope(
                **{
                    **env.context.scope.model_dump(),
                    "subject_type": "person",
                    "subject_id": "subject_fixture",
                }
            )
        }
    )
    await RecoveryService(env.engine, env.iam.authorization).initialize_fresh(source_context)
    version = detail.versions[0]
    source = await env.agents.test(
        source_context,
        version.version_id,
        AgentTestInput(
            revision=version.revision,
            input={"request": "邮箱 private@example.test，电话 13812345678"},
            idempotency_key="source-content",
        ),
    )
    await execute_message(
        env.runs,
        TaskEnvelope(channel_id=source_context.scope.channel_id, run_id=source.run_id),
        "source-worker",
        env.runtime,
    )
    copied = await env.evaluations.from_run(
        env.context,
        dataset.dataset_id,
        RunCaseInput(
            revision=dataset.revision,
            run_id=source.run_id,
            case_key="feedback",
            title="用户反馈",
            input_paths=["request"],
            assertions=[
                {"kind": "equal", "name": "预期答复", "path": "data.answer", "expected": "验证完成"}
            ],
            label_source="经审阅的真实运行反馈",
            review={"decision": "approved", "reason": "已选择必要字段并脱敏"},
            version_label="反馈新版本",
        ),
    )
    sample = copied.versions[-1].cases[-1]
    assert "private@example.test" not in sample.payload.model_dump_json()
    assert "13812345678" not in sample.payload.model_dump_json()
    task = await env.evaluations.create(
        env.context,
        EvaluationCreate(
            name="反馈回归",
            dataset_version_id=copied.current_version_id,
            candidates=[{"version_id": version.version_id, "revision": version.revision}],
            budget={"max_runs": 3, "token_limit": 1000000},
        ),
    )
    await finish(env, task)

    class SourceAuthorization:
        async def require(self, context, action, identifier):
            await env.iam.authorization.boundary(context, action, "run", identifier)

    await DeletionService(env.engine, SourceAuthorization()).mark(
        source_context, ContentRef("run", source.run_id), "个人删除请求"
    )
    report = await env.evaluations.comparison(env.context, task.evaluation_id)
    assert not report.reproducible
    result = next(r for r in report.results if r["case_id"] == sample.case_id)
    assert result["state"] == "INVALID"
    with pytest.raises(ServiceError) as deleted:
        await env.runs.detail(env.context, result["run_id"])
    assert deleted.value.code == "CONTENT_DELETED"
    await env.cleanup.clean(env.context, ContentRef("run", result["run_id"]))
    await env.cleanup.clean(env.context, ContentRef("evaluation_case", sample.case_id))
    async with env.engine.connect() as connection:
        assert (
            await repository("evaluation_cases", env.context.scope).get(connection, sample.case_id)
        )["payload"] is None


async def test_worker_sweep_reserves_budget_and_keeps_full_denominator(evaluation_env):
    env = evaluation_env
    _, _, task = await prepare(env, token_limit=env.definition.limits.token_limit)
    await env.evaluations.sweep(env.context.scope.channel_id)
    report = await env.evaluations.comparison(env.context, task.evaluation_id)
    running = [r for r in report.results if r["state"] == "RUNNING"]
    assert len(running) == 1
    await execute_message(
        env.runs,
        TaskEnvelope(channel_id=env.context.scope.channel_id, run_id=running[0]["run_id"]),
        "stored-evaluation-worker",
        env.runtime,
    )
    await env.evaluations.sweep(env.context.scope.channel_id)
    report = await env.evaluations.comparison(env.context, task.evaluation_id)
    assert report.complete and report.cost["run_count"] == 1
    assert report.candidates[0]["counts"]["UNEXECUTED"] == 1
    assert report.candidates[0]["pass_rate"] == 0.5
    assert not report.candidates[0]["release_passed"]


async def test_human_disagreement_is_independent_and_blocks_release(evaluation_env):
    from creativity_service.modules.evaluations.schemas import EvaluationReview

    env = evaluation_env
    _, _, task = await prepare(env, 1)
    await finish(env, task)
    await approve(env, task)
    report = await env.evaluations.comparison(env.context, task.evaluation_id)
    assert report.candidates[0]["release_passed"]
    result = report.results[0]
    await env.evaluations.review_result(
        env.context,
        result["result_id"],
        EvaluationReview(
            revision=result["revision"], label={"decision": "disputed", "reason": "预期需确认"}
        ),
    )
    await approve(env, task)
    report = await env.evaluations.comparison(env.context, task.evaluation_id)
    assert report.results[0]["judgment"]["passed"]
    assert report.results[0]["human_label"]["decision"] == "disputed"
    assert report.candidates[0]["human_disagreements"] == 1
    assert not report.candidates[0]["release_passed"]


async def test_channel_scope_and_sensitive_permission_are_independent(evaluation_env):
    from creativity_service.core.primitives import ServiceError
    from creativity_service.modules.iam.schemas import GrantInput, MembershipInput
    from tests.integration.iam.conftest import create_user, enter

    env = evaluation_env
    _, dataset, task = await prepare(env, 1)
    await finish(env, task)
    account, (_, session) = await create_user(env.iam, env.admin, "evaluation-reader")
    await env.iam.access.put_member(
        env.tenant.manager,
        env.context.scope.channel_id,
        account.user_id,
        MembershipInput(roles=["builder"], environments=["prod"]),
    )
    await env.iam.access.put_grant(
        env.tenant.manager,
        env.context.scope.channel_id,
        "evaluation-reader-grant",
        GrantInput(
            grantee_type="account",
            grantee_id=account.user_id,
            resource_type="evaluation",
            resource_id="*",
            allowed_actions=["evaluation:read", "evaluation:content", "evaluation:manage"],
            environments=["prod"],
        ),
    )
    _, reader = await enter(env.iam, session, env.context.scope.channel_id, "prod")
    available = await env.evaluations.list_evaluations(env.context)
    assert "create" in {action.action_key for action in available.actions}
    restricted = await env.evaluations.list_evaluations(reader.context)
    assert "create" not in {action.action_key for action in restricted.actions}
    view = await env.evaluations.dataset(reader.context, dataset.dataset_id)
    assert view.versions[0].cases[0].payload is None
    report = await env.evaluations.comparison(reader.context, task.evaluation_id)
    assert report.results[0]["judgment"] is None and report.human_review is None
    with pytest.raises(ServiceError) as sensitive:
        await env.evaluations.require(reader.context, "evaluation:content", task.evaluation_id)
    assert sensitive.value.status == 403
    foreign = env.context.model_copy(
        update={"scope": env.context.scope.model_copy(update={"channel_id": "unrelated_channel"})}
    )
    with pytest.raises(ServiceError):
        await env.evaluations.dataset(foreign, dataset.dataset_id)
    response = await env.client.post(
        "/admin/v1/evaluation-datasets",
        json={
            "name": "不合法范围",
            "scenario": "隔离",
            "owner": "验证",
            "applicability": "范围校验",
            "channel_id": "unrelated_channel",
        },
    )
    assert response.status_code == 422
