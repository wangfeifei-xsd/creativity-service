"""配置式判定和有界导入的确定性规则。"""

import csv
import io
import json

import pytest
from pydantic import ValidationError

from creativity_service.core.primitives import ServiceError, utcnow
from creativity_service.modules.evaluations.imports import preview
from creativity_service.modules.evaluations.judges import judge
from creativity_service.modules.evaluations.schemas import CaseInput, ImportInput


def sample(**changes):
    return CaseInput.model_validate(
        {
            "case_key": "a",
            "title": "数值样本",
            "input": {},
            "assertions": [
                {
                    "kind": "numeric",
                    "name": "精度",
                    "path": "data.total",
                    "expected": 12.5,
                    "tolerance": 0.01,
                }
            ],
            "label_source": "接入方固定预期",
            **changes,
        }
    )


def test_deterministic_failure_wins_over_semantic_score():
    result = judge(
        sample(),
        {"data": {"total": 13}},
        None,
        set(),
        semantic={
            "score": 1,
            "configuration": {"judge": "fixture"},
            "run_id": "judge_fixture",
            "usage": {"tokens": 10},
        },
    )
    assert not result["passed"] and result["critical"]
    assert result["semantic"]["score"] == 1
    assert judge(sample(), {"data": {"total": 12.501}}, None, set())["passed"]
    assert not judge(sample(), {"data": {"total": True}}, None, set())["passed"]


def test_missing_values_cannot_equal_null_and_citations_need_authorized_evidence():
    value = sample(
        assertions=[{"kind": "equal", "name": "缺失判断", "path": "data.value", "expected": None}]
    )
    assert not judge(value, {"data": {}}, None, set())["passed"]
    citation = sample(
        assertions=[{"kind": "citation", "name": "来源可验证", "path": "evidence_refs"}]
    )
    assert not judge(citation, {"evidence_refs": ["invented"]}, None, {"actual"})["passed"]
    assert judge(citation, {"evidence_refs": [{"evidence_id": "actual"}]}, None, {"actual"})[
        "passed"
    ]


def test_import_csv_mapping_unicode_newlines_and_bad_row():
    stream = io.StringIO()
    writer = csv.DictWriter(stream, ["编号", "title", "input", "assertions", "label_source"])
    writer.writeheader()
    data = sample().model_dump(mode="json")
    writer.writerow(
        {
            "编号": "a",
            "title": "中文，带换行\n样本",
            "input": "{}",
            "assertions": json.dumps(data["assertions"]),
            "label_source": "人工固定预期",
        }
    )
    result = preview(
        ImportInput(
            format="csv",
            mapping={"编号": "case_key"},
            content=stream.getvalue(),
            revision=1,
            version_label="第一版",
            captured_at=utcnow(),
        )
    )
    assert result.valid_count == 1 and result.rows[0].case.title == "中文，带换行\n样本"


def test_forbid_schema_network_reference_and_empty_assertions():
    with pytest.raises((ValidationError, ServiceError)):
        sample(
            assertions=[
                {
                    "kind": "schema",
                    "name": "结构",
                    "expected": {"$ref": "https://example.test/schema"},
                }
            ]
        )
    with pytest.raises(ValidationError):
        sample(assertions=[])


@pytest.mark.parametrize("kind", ["equal", "one_of", "subset"])
def test_nested_json_boolean_is_not_number(kind):
    expected = {"value": 1} if kind == "equal" else [{"value": 1}]
    value = {"value": True} if kind != "subset" else [{"value": True}]
    case = sample(assertions=[{"kind": kind, "name": "精确值", "expected": expected}])
    assert not judge(case, value, None, set())["passed"]


def test_reject_nonfinite_expectation_and_malformed_citation():
    with pytest.raises(ValidationError):
        sample(assertions=[{"kind": "numeric", "name": "有限值", "expected": float("inf")}])
    case = sample(assertions=[{"kind": "citation", "name": "有效引用"}])
    assert not judge(case, [{"evidence_id": []}], None, {"known"})["passed"]
