"""字段来源、分支终止和冻结边界的确定性验证。"""

import json
from datetime import timedelta

import pytest
from pydantic import ValidationError

from creativity_service.core.context import Scope
from creativity_service.core.primitives import digest, utcnow
from creativity_service.modules.agents.registry import legacy_templates, templates
from creativity_service.modules.agents.schemas import (
    AgentBindings,
    AgentCondition,
    AgentDefinition,
    AgentEdge,
    AgentLimits,
    AgentStep,
    FrozenExecutionSpec,
    InputSource,
)
from creativity_service.modules.agents.validation import compatible, static_issues


def changed(**values):
    return templates()[0].definition.model_copy(update=values)


def codes(definition):
    return [issue.message for issue in static_issues(definition)]


def test_generic_workflows_and_legacy_entrypoints_are_separate():
    all_templates = templates()
    assert {t.workflow_type for t in all_templates} == {
        "structured",
        "template",
        "tool_loop",
        "stateful",
    }
    assert {t.key for t in all_templates if t.workflow_type == "template"} == {"workflow.v1"}
    assert {t.key for t in legacy_templates()} == {
        "matching.v1",
        "risk.v1",
        "analysis.v1",
    }
    assert not codes(all_templates[0].definition)


def test_shared_route_has_one_dependency_without_rejecting_distinct_uses():
    bindings = AgentBindings(
        prompt_id="prompt", model_route_id="shared_route", embedding_route_id="shared_route"
    )
    assert bindings.ids() == ["prompt", "shared_route"]
    assert not codes(changed(bindings=bindings))


@pytest.mark.parametrize("field", ["tool_ids", "skill_ids"])
def test_duplicate_resource_within_one_binding_is_rejected(field):
    issues = static_issues(
        changed(bindings=AgentBindings.model_validate({field: ["same", "same"]}))
    )
    assert any(
        issue.path == f"bindings.{field}" and "不能重复" in issue.message for issue in issues
    )


def test_generic_workflow_accepts_configured_steps_and_legacy_keeps_topology():
    base = next(t.definition for t in templates() if t.key == "workflow.v1")
    first = base.steps[0].model_copy(update={"key": "collect", "name": "整理资料"})
    second = base.steps[0].model_copy(update={"key": "summarize", "name": "生成摘要"})
    configured = base.model_copy(
        update={
            "start_step": "collect",
            "steps": (first, second),
            "edges": (
                AgentEdge(source="collect", target="summarize"),
                AgentEdge(source="summarize", target="END"),
            ),
        }
    )
    assert not codes(configured)
    for old in legacy_templates():
        assert not codes(old.definition)
        assert codes(configured.model_copy(update={"entrypoint": old.key}))


@pytest.mark.parametrize(
    "schema",
    [
        {"type": "object", "$ref": "https://example.com/schema"},
        {"type": "object", "$ref": "#"},
        {"type": "unknown"},
        {"type": "object", "required": "name"},
    ],
)
def test_reject_unverifiable_schema_without_remote_resolution(schema):
    assert codes(changed(input_schema=schema))


def test_missing_source_incompatible_output_and_required_input():
    definition = changed()
    step = definition.steps[0]
    for inputs in (
        {},
        {"request": InputSource(source="input", path="missing")},
        {"request": InputSource(source="constant", value=3)},
    ):
        assert codes(changed(steps=(step.model_copy(update={"inputs": inputs}),)))
    assert codes(changed(steps=(step.model_copy(update={"output_schema": {"type": "object"}}),)))


@pytest.mark.parametrize("status_schema", [True, False, {"type": "string", "enum": [{}]}])
def test_invalid_business_status_returns_issues_without_schema_crash(status_schema):
    definition = changed()
    definition.output_schema["properties"]["business_status"] = status_schema
    assert codes(definition)


def test_unproven_schema_constraints_and_boolean_subschemas():
    assert not compatible({"type": "object"}, {"type": "object", "maxProperties": 1})
    assert not compatible(
        {"type": "object"},
        {"type": "object", "properties": {"value": {"type": "integer"}}},
    )
    assert not compatible({"type": "array", "items": True}, {"type": "array", "items": False})
    assert compatible(
        {"type": "object", "properties": {"value": {"type": "string"}}},
        {"type": "object", "properties": {"value": True}},
    )


def test_branch_must_be_exhaustive_and_source_must_dominate_consumer():
    base = templates()[0].definition
    first = base.steps[0].model_copy(update={"key": "first", "name": "第一步"})
    second = base.steps[0].model_copy(update={"key": "second", "name": "第二步"})
    third = base.steps[0].model_copy(
        update={
            "key": "third",
            "name": "第三步",
            "inputs": {"request": InputSource(source="step", step="second", path="schema_version")},
        }
    )
    branch = AgentEdge(
        source="first",
        target="second",
        condition=AgentCondition(path="business_status", value="COMPLETED"),
    )
    config = changed(
        workflow_type="stateful",
        entrypoint="stateful.v1",
        start_step="first",
        steps=(first, second, third),
        edges=(
            branch,
            AgentEdge(source="first", target="third", otherwise=True),
            AgentEdge(source="second", target="third"),
            AgentEdge(source="third", target="END"),
        ),
    )
    assert any("所有路径" in message for message in codes(config))
    assert any(
        "兜底" in message
        for message in codes(
            config.model_copy(update={"edges": tuple(e for e in config.edges if not e.otherwise)})
        )
    )


def test_cycle_requires_registered_type_limits_and_exit():
    base = templates()[0].definition
    loop = AgentEdge(
        source="answer",
        target="answer",
        condition=AgentCondition(path="business_status", value="PARTIAL"),
    )
    ending = AgentEdge(source="answer", target="END", otherwise=True)
    assert any("不允许循环" in message for message in codes(changed(edges=(loop, ending))))
    assert not codes(
        changed(workflow_type="stateful", entrypoint="stateful.v1", edges=(loop, ending))
    )
    assert any(
        "终止出口" in message
        for message in codes(
            changed(
                workflow_type="stateful",
                entrypoint="stateful.v1",
                edges=(AgentEdge(source="answer", target="answer"),),
            )
        )
    )
    assert codes(base.model_copy(update={"limits": AgentLimits(deadline_seconds=20)}))
    with pytest.raises(ValidationError):
        AgentLimits(max_iterations=0)


def test_output_contract_schema_and_no_arbitrary_entrypoint():
    assert codes(changed(entrypoint="user_script"))
    assert codes(
        changed(output_schema={"type": "object", "properties": {"text": {"type": "string"}}})
    )
    with pytest.raises(ValidationError):
        AgentDefinition.model_validate({**changed().model_dump(), "channel_id": "spoofed"})
    with pytest.raises(ValidationError):
        AgentStep.model_validate({**changed().steps[0].model_dump(), "code": "print('unsafe')"})


def test_frozen_execution_spec_returns_copies_and_refuses_assignment():
    definition = changed()
    candidate = FrozenExecutionSpec(
        snapshot_id="candidate",
        scope=Scope(channel_id="channel_a", environment="test"),
        agent_id="agent_a",
        agent_name="智能体",
        agent_code="task",
        source_version_id="draft",
        source_revision=1,
        purpose="evaluation",
        content_digest=digest({}),
        dependencies_digest=digest([]),
        candidate_digest=digest("candidate"),
        captured_at=utcnow(),
        payload_json=json.dumps({"definition": definition.model_dump(mode="json"), "versions": []}),
    )
    candidate.definition.output_schema.clear()
    assert candidate.definition.output_schema
    with pytest.raises(ValidationError):
        candidate.payload_json = "{}"


@pytest.mark.parametrize(
    "change",
    [
        {"passed": False},
        {"report_ids": ("another",)},
        {"expires_at": utcnow() - timedelta(seconds=1)},
        {"environment": "dev"},
        {"channel_id": "other"},
        {"content_digest": digest("other")},
        {"dependencies_digest": digest("other")},
    ],
)
def test_evaluation_evidence_checks_all_binding_dimensions(change):
    from creativity_service.core.context import AuthContext
    from creativity_service.core.primitives import ServiceError
    from creativity_service.modules.releases.checks import check_evidence
    from creativity_service.modules.releases.ports import EvaluationEvidence

    context = AuthContext(
        scope=Scope(channel_id="channel_a", environment="prod"),
        principal_type="management",
        actor_id="actor",
        principal_id="actor",
        request_id="request",
    )
    evidence = EvaluationEvidence(
        channel_id="channel_a",
        agent_id="agent",
        environment="prod",
        content_digest=digest("content"),
        dependencies_digest=digest("deps"),
        report_digest=digest("report"),
        report_ids=("report",),
        expires_at=utcnow() + timedelta(hours=1),
        passed=True,
    )
    with pytest.raises(ServiceError):
        check_evidence(
            evidence.model_copy(update=change),
            context,
            "agent",
            evidence.content_digest,
            evidence.dependencies_digest,
            ("report",),
        )


async def test_caller_key_budget_is_enforced_without_changing_published_dependencies():
    from decimal import Decimal
    from types import SimpleNamespace

    from creativity_service.core.context import AuthContext
    from creativity_service.core.primitives import ServiceError
    from creativity_service.modules.budgets.services import BudgetService
    from creativity_service.modules.releases.checks import check_budget

    context = AuthContext(
        scope=Scope(channel_id="channel_a", environment="test"),
        principal_type="management",
        actor_id="actor",
        principal_id="actor",
        request_id="request",
    )
    caller = context.model_copy(update={"key_id": "key_a"})
    channel_policy = {
        "id": "channel_budget",
        "version_id": "budget_version",
        "scope_type": "channel",
        "scope_id": "channel_a",
        "unit": "tokens",
        "mode": "HARD",
        "currency": None,
        "limit_value": Decimal(100000),
    }
    key_policy = {**channel_policy, "id": "key_budget", "scope_type": "key", "scope_id": "key_a"}

    class BudgetReader:
        snapshot = staticmethod(BudgetService.snapshot)

        def require(self, uow, context, *, read_only=False):
            pass

        async def policies(self, uow):
            return [channel_policy, key_policy]

        async def prices(self, uow, model_ids, at):
            return {}

        async def exposure_data(self, uow, policies, now):
            return {}

        async def estimate(self, uow, plan, *, prices=None):
            return {}, None, None

        async def exposure(self, uow, policy, now, data=None):
            return Decimal(0)

    service, uow = BudgetReader(), object()
    model = SimpleNamespace(model_id="model", connection_id="connection")
    published = await check_budget(service, uow, context, "agent", changed(), [model])
    requested = await check_budget(service, uow, caller, "agent", changed(), [model])
    assert published == requested and len(published) == 1
    key_policy["limit_value"] = Decimal(100)
    with pytest.raises(ServiceError) as exc:
        await check_budget(service, uow, caller, "agent", changed(), [model])
    assert exc.value.code == "BUDGET_NOT_EXECUTABLE"


def test_legacy_definition_golden_files_and_importable_examples():
    from pathlib import Path

    from creativity_service.modules.agents.schemas import AgentCreate

    for item in legacy_templates():
        stored = Path("contracts/agents/legacy-v1") / f"{item.key}.json"
        current = item.definition.model_dump(mode="json")
        assert current["bindings"].pop("skill_loading") == []
        assert current["bindings"].pop("embedding_route_version") is None
        for step in current["steps"]:
            assert step.pop("operator") is None
        assert json.loads(stored.read_text()) == current
    for path in Path("examples/agents").glob("*.json"):
        body = AgentCreate.model_validate_json(path.read_text())
        assert body.definition.entrypoint in {"workflow.v1", "structured.v1"}
        assert not codes(body.definition)
