"""通用确定性判定；人工与语义结论独立，关键失败不能被平均分抵消。"""

import json
from decimal import Decimal
from typing import Any

from jsonschema import Draft202012Validator

from creativity_service.modules.evaluations.schemas import CaseInput

CRITICAL = frozenset(
    {"cross_scope", "secret_leak", "fabricated_entity", "calculation", "authorization"}
)
MISSING = object()


def field(value: Any, path: str) -> Any:
    for part in path.split(".") if path else []:
        if isinstance(value, dict):
            value = value.get(part, MISSING)
        elif isinstance(value, list) and part.isdigit() and int(part) < len(value):
            value = value[int(part)]
        else:
            return MISSING
    return value


def same_json(value: Any, expected: Any) -> bool:
    """递归比较 JSON 值，避免布尔值与数值在容器中被视为相同。"""
    if type(value) is not type(expected):
        return False
    if isinstance(value, dict):
        return value.keys() == expected.keys() and all(
            same_json(v, expected[k]) for k, v in value.items()
        )
    if isinstance(value, list):
        return len(value) == len(expected) and all(
            same_json(v, e) for v, e in zip(value, expected, strict=True)
        )
    return bool(value == expected)


def judge(
    case: CaseInput,
    output: Any,
    error_code: str | None,
    evidence_ids: set[str],
    *,
    semantic: dict[str, Any] | None = None,
) -> dict[str, Any]:
    checks = []
    if case.expected_error:
        checks.append(
            {
                "name": "预期拒绝或故障",
                "passed": error_code == case.expected_error,
                "category": "authorization"
                if case.expected_error in {"FORBIDDEN", "TOOL_FORBIDDEN", "SCOPE_MISMATCH"}
                else "quality",
            }
        )
    elif error_code:
        checks.append({"name": "运行完成", "passed": False, "category": "quality"})
    if output is not None:
        for assertion in case.assertions:
            value = field(output, assertion.path)
            passed = False
            if value is not MISSING:
                if assertion.kind == "schema":
                    passed = Draft202012Validator(assertion.expected).is_valid(value)
                elif assertion.kind == "equal":
                    passed = same_json(value, assertion.expected)
                elif assertion.kind == "numeric":
                    passed = type(value) in {int, float} and abs(
                        Decimal(str(value)) - Decimal(str(assertion.expected))
                    ) <= Decimal(str(assertion.tolerance))
                elif assertion.kind == "one_of":
                    passed = any(same_json(value, v) for v in assertion.expected)
                elif assertion.kind == "subset":
                    passed = isinstance(value, list) and all(
                        any(same_json(v, e) for e in assertion.expected) for v in value
                    )
                elif assertion.kind == "forbidden":
                    text = json.dumps(value, ensure_ascii=False)
                    passed = all(str(v) not in text for v in assertion.expected)
                elif assertion.kind == "citation":
                    passed = (
                        isinstance(value, list)
                        and bool(value)
                        and all(
                            isinstance(v.get("evidence_id") if isinstance(v, dict) else v, str)
                            and (v.get("evidence_id") if isinstance(v, dict) else v) in evidence_ids
                            for v in value
                        )
                    )
            category = "calculation" if assertion.kind == "numeric" else assertion.category
            checks.append({"name": assertion.name, "passed": passed, "category": category})
    elif not case.expected_error:
        checks.extend(
            {
                "name": a.name,
                "passed": False,
                "category": "calculation" if a.kind == "numeric" else a.category,
            }
            for a in case.assertions
        )
    violations = [c for c in checks if not c["passed"]]
    return {
        "passed": bool(checks) and not violations,
        "checks": checks,
        "violations": violations,
        "critical": any(c["category"] in CRITICAL for c in violations),
        "semantic": semantic,
        "human_required": not checks,
    }
