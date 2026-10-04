"""从全部样本、历次运行和账本重建报告，缺失或未执行不能算通过。"""

from datetime import UTC
from decimal import Decimal
from typing import Any

from creativity_service.core.context import AuthContext
from creativity_service.core.database import Repository, UnitOfWork
from creativity_service.core.deletion import ContentRef, DeletionGuard
from creativity_service.core.primitives import ServiceError, digest
from creativity_service.modules.evaluations.repositories import repository, required
from creativity_service.modules.runs.tables import metadata as run_metadata
from creativity_service.modules.usage.tables import metadata as usage_metadata

FINAL_RESULTS = {"PASSED", "FAILED", "INVALID", "CANCELLED", "UNEXECUTED"}
LABELS = {
    "PASSED": "通过",
    "FAILED": "未通过",
    "INVALID": "样本无效",
    "CANCELLED": "未完成",
    "UNEXECUTED": "未执行",
    "PENDING": "未执行",
    "DISPATCHING": "派发中",
    "RUNNING": "执行中",
}


async def build_report(
    service: Any,
    uow: UnitOfWork,
    context: AuthContext,
    task: dict[str, Any],
    *,
    compare_baseline: bool = True,
) -> dict[str, Any]:
    scope = context.scope
    version = await required(
        uow.connection, scope, "evaluation_dataset_versions", task["dataset_version_id"]
    )
    rows = await repository("evaluation_results", scope).find(
        uow.connection, evaluation_id=task["id"]
    )
    latest: dict[tuple[str, str], dict[str, Any]] = {}
    for row in sorted(rows, key=lambda r: r["attempt_number"]):
        latest[(row["candidate_id"], row["case_id"])] = row
    reproducible = task["execution_mode"] == "fixture"
    sources_valid = True
    stored_report = await repository("evaluation_reports", scope).get(uow.connection, task["id"])
    if stored_report and not stored_report["reproducible"] and not stored_report["payload"]:
        sources_valid = reproducible = False
    try:
        await DeletionGuard(scope).check(uow, [ContentRef("evaluation", task["id"])])
    except ServiceError as exc:
        if exc.code != "CONTENT_DELETED":
            raise
        sources_valid = reproducible = False
    case_rows = await repository("evaluation_cases", scope).get_many(
        uow.connection, [*version["case_ids"], *(r["case_id"] for r in rows)]
    )
    if set(version["case_ids"]) - case_rows.keys():
        raise ServiceError("NOT_FOUND", "评测样本不存在", 404)
    fixed_cases = [case_rows[i] for i in version["case_ids"]]
    validity = await service.valid_cases(uow, context, list(case_rows.values()))
    content_digest = digest(
        {
            "cases": [c["payload"] for c in fixed_cases],
            "reference_versions": version["reference_versions"],
            "reference_digests": version["reference_digests"],
            "captured_at": version["captured_at"].astimezone(UTC).isoformat(),
        }
    )
    if (
        task["dataset_digest"] != version["content_digest"]
        or content_digest != version["content_digest"]
    ):
        sources_valid = reproducible = False
    from creativity_service.modules.agents.repositories import repository as agent_repository

    references = await agent_repository("resource_versions", scope).get_many(
        uow.connection, version["reference_digests"]
    )
    for identifier, expected in version["reference_digests"].items():
        reference = references.get(identifier)
        if (
            not reference
            or reference["state"] != "PUBLISHED"
            or reference["content_digest"] != expected
        ):
            sources_valid = reproducible = False
    results = []
    for row in latest.values():
        case = case_rows[row["case_id"]]
        valid = validity[case["id"]]
        if not valid:
            sources_valid = reproducible = False
        judgment = row["judgment"] if valid and sources_valid else None
        results.append(
            {
                "result_id": row["id"],
                "case_id": row["case_id"],
                "case_key": case["case_key"] if valid else None,
                "title": case["title"] if valid else "来源已删除的样本",
                "labels": case["payload"].get("labels", []) if valid else [],
                "candidate_id": row["candidate_id"],
                "run_id": row["run_id"],
                "attempt_number": row["attempt_number"],
                "revision": row["revision"],
                "state": row["state"] if valid else "INVALID",
                "state_label": LABELS[row["state"]] if valid else "样本无效",
                "judgment": judgment,
                "human_label": row["human_label"] if valid and sources_valid else None,
            }
        )
    complete = (
        len(latest) == len(version["case_ids"]) * len(task["candidate_snapshots"])
        and all(r["state"] in FINAL_RESULTS for r in results)
        and task["state"] == "COMPLETED"
    )
    baseline = None
    if compare_baseline and task["baseline_evaluation_id"]:
        baseline_task = await required(
            uow.connection, scope, "evaluations", task["baseline_evaluation_id"]
        )
        # 重新读取基线样本、结果及来源，重跑或删除后不能沿用旧缓存。
        baseline_report = await build_report(
            service, uow, context, baseline_task, compare_baseline=False
        )
        matching = [
            c
            for c in baseline_report["candidates"]
            if c["candidate_id"] == task["baseline_candidate_id"]
        ]
        comparable = (
            baseline_task["dataset_digest"] == task["dataset_digest"]
            and baseline_task["execution_mode"] == task["execution_mode"]
            and baseline_task["state"] == "COMPLETED"
            and baseline_report["complete"]
            and baseline_report["reproducible"]
            and sources_valid
        )
        baseline = {
            "evaluation_id": baseline_task["id"],
            "name": baseline_task["name"],
            "candidate_id": task["baseline_candidate_id"],
            "comparable": comparable,
            "pass_rate": matching[0]["pass_rate"] if matching and comparable else None,
            "report_digest": digest(baseline_report),
        }
    elif compare_baseline and task["baseline_candidate_id"]:
        selected = [r for r in results if r["candidate_id"] == task["baseline_candidate_id"]]
        baseline = {
            "evaluation_id": task["id"],
            "candidate_id": task["baseline_candidate_id"],
            "name": "同批基线",
            "comparable": task["execution_mode"] == "fixture" and complete and sources_valid,
            "pass_rate": sum(r["state"] == "PASSED" for r in selected) / len(selected)
            if selected
            else None,
        }
    baseline_results: list[dict[str, Any]] = []
    if baseline and baseline["comparable"]:
        baseline_results = (
            baseline_report["results"]
            if task["baseline_evaluation_id"]
            else [r for r in results if r["candidate_id"] == task["baseline_candidate_id"]]
        )
    baseline_by_case = {
        r["case_key"]: r
        for r in baseline_results
        if r["candidate_id"] == task["baseline_candidate_id"]
    }
    for result in results:
        previous = baseline_by_case.get(result["case_key"])
        result["baseline_state"] = previous["state"] if previous else None
        result["difference_label"] = (
            "无可比结果"
            if previous is None
            else "保持一致"
            if previous["state"] == result["state"]
            else "新增失败"
            if previous["state"] == "PASSED"
            else "新增通过"
            if result["state"] == "PASSED"
            else "结果变化"
        )
    summaries = []
    for candidate in task["candidate_snapshots"]:
        selected = [r for r in results if r["candidate_id"] == candidate["snapshot_id"]]
        counts = {s: sum(r["state"] == s for r in selected) for s in LABELS}
        total = len(version["case_ids"])
        rate = counts["PASSED"] / total if total else 0
        critical = sum(
            bool(r["judgment"] and r["judgment"].get("critical"))
            for r in rows
            if r["candidate_id"] == candidate["snapshot_id"]
        )
        regression = (
            rate - baseline["pass_rate"]
            if baseline and baseline["comparable"] and baseline["pass_rate"] is not None
            else None
        )
        review = task["human_review"]
        human_disagreements = sum(
            bool(r["human_label"] and r["human_label"]["decision"] != "approved") for r in selected
        )
        labels_approved = True
        for r in selected:
            sample = case_rows[r["case_id"]]
            label = (sample["payload"] or {}).get("human_label")
            labels_approved = labels_approved and bool(label and label["decision"] == "approved")
        passed = (
            complete
            and sources_valid
            and not critical
            and not human_disagreements
            and counts["INVALID"] == 0
            and counts["CANCELLED"] == 0
            and counts["UNEXECUTED"] == 0
            and rate >= task["config"]["minimum_pass_rate"]
            and (
                not baseline
                or baseline["comparable"]
                and regression is not None
                and regression >= -task["config"]["maximum_regression"]
            )
        )
        summaries.append(
            {
                "candidate_id": candidate["snapshot_id"],
                "agent_id": candidate["agent_id"],
                "agent_name": candidate["agent_name"],
                "version_id": candidate["version_id"],
                "version_label": candidate["version_label"],
                "content_digest": candidate["content_digest"],
                "dependencies_digest": candidate["dependencies_digest"],
                "dependencies": candidate["dependencies"],
                "total": total,
                "counts": counts,
                "pass_rate": rate,
                "regression": regression,
                "critical_failures": critical,
                "human_disagreements": human_disagreements,
                "quality_passed": passed,
                "release_passed": passed
                and reproducible
                and candidate["published_dependencies"]
                and task["config"]["release_target"]
                and labels_approved
                and bool(review and review["decision"] == "approved"),
            }
        )
    costs: dict[str, Decimal] = {}
    unknown = pending = tokens = 0
    latencies = []
    run_ids = {r["run_id"] for r in rows if r["run_id"]}
    runs = await Repository(run_metadata.tables["runs"], scope).get_many(uow.connection, run_ids)
    for run in runs.values():
        if run["completed_at"] and run["created_at"]:
            latencies.append((run["completed_at"] - run["created_at"]).total_seconds() * 1000)
    usages = await Repository(usage_metadata.tables["usage_records"], scope).find_many(
        uow.connection, "run_id", run_ids
    )
    for usage in usages:
        pending += usage["state"] != "SETTLED"
        if usage["amount"] is None or usage["currency"] is None:
            unknown += 1
        else:
            currency = usage["currency"]
            costs[currency] = costs.get(currency, Decimal(0)) + usage["amount"]
        if usage["input_tokens"] is not None and usage["output_tokens"] is not None:
            tokens += usage["input_tokens"] + usage["output_tokens"]
    warnings = []
    if task["config"].get("dispatch_error"):
        warnings.append(task["config"]["dispatch_error"]["message"])
    if task["execution_mode"] == "live_readonly":
        warnings.append("实时只读评测的外部数据可能变化，不作为固定数据发布证据")
    if not sources_valid:
        warnings.append("来源已删除，相关内容已隐藏，报告可复现性已变化")
    if not complete:
        warnings.append("任务未全部完成，当前为部分报告")
    if baseline and not baseline["comparable"]:
        warnings.append("基线未完成、来源失效或比较条件变化，无法比较")
    return {
        "report_id": task["id"],
        "evaluation_id": task["id"],
        "revision": task["revision"],
        "reproducible": reproducible and sources_valid,
        "complete": complete,
        "candidates": summaries,
        "results": results,
        "cost": {
            "run_count": len(run_ids),
            "amounts": {k: str(v) for k, v in costs.items()},
            "unknown_count": unknown,
            "pending_count": pending,
            "known_tokens": tokens,
        },
        "latency": {
            "completed_runs": len(latencies),
            "mean_ms": sum(latencies) / len(latencies) if latencies else None,
            "total_ms": sum(latencies) if latencies else None,
        },
        "baseline": baseline,
        "human_review": task["human_review"] if sources_valid else None,
        "warnings": warnings,
    }


async def save_report(
    service: Any, uow: UnitOfWork, context: AuthContext, task: dict[str, Any]
) -> dict[str, Any]:
    payload = await build_report(service, uow, context, task)
    values = {
        "evaluation_id": task["id"],
        "report_digest": digest(payload),
        "payload": payload,
        "reproducible": payload["reproducible"],
    }
    repo = repository("evaluation_reports", context.scope)
    current = await repo.get(uow.connection, task["id"])
    if current:
        await repo.change(uow, task["id"], current["revision"], values)
    else:
        await repo.add(uow, task["id"], values)
    return {**payload, "report_digest": values["report_digest"]}
