"""验收环境、故障矩阵与切换关口；缺少证据时保持阻断。"""

import json
import xml.etree.ElementTree as ET
from collections import Counter
from datetime import UTC, datetime

from scripts.render_acceptance import ROOT, classify, results

FAULTS = {
    "受理提交后队列不可用": [
        "tests/integration/runs/test_runs.py::test_admission_commit_without_publish_and_queue_failure"
    ],
    "独立 Worker 重复投递": [
        "tests/integration/runs/test_queue_and_exit.py::test_real_celery_duplicate_messages_are_only_wakeups"
    ],
    "外部返回后进程退出": [
        "tests/integration/runs/test_queue_and_exit.py::test_process_exit_after_remote_return_keeps_attempt_and_pending_cost"
    ],
    "Redis 不可用与账号撤销": [
        "tests/integration/iam/test_accounts.py::test_redis_unavailable_is_503_and_errors_are_distinct",
        "tests/integration/iam/test_accounts.py::test_reset_disable_and_logout_block_even_when_redis_cleanup_fails",
    ],
    "取消、删除后的迟到响应": [
        "tests/integration/runtime/test_boundaries.py::test_deletion_drops_late_text_but_settles_usage",
        "tests/integration/runtime/test_execution.py::test_cancel_inflight_preserves_usage_without_success",
    ],
    "旧备份恢复与独立删除清单": [
        "tests/integration/data_lifecycle/test_lifecycle.py::test_restore_old_database_and_objects_replays_latest_manifest",
        "tests/integration/data_lifecycle/test_lifecycle.py::test_recovery_rejects_stale_or_unavailable_ledger",
    ],
    "重复用量与下一次预算预占": [
        "tests/integration/usage/test_ledger.py::test_simultaneous_final_callback_and_next_reservation_preserve_exposure",
        "tests/integration/usage/test_ledger.py::test_cumulative_dedup_final_precedence_and_late_correction",
    ],
}


def write_json(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n")


def schema_evidence(output, connection):
    from sqlalchemy import inspect

    from creativity_service.core.database.audit import (
        audit_catalog,
        audit_database,
        audit_definitions,
        audit_sources,
    )

    inspector = inspect(connection)
    tables = inspector.get_table_names()
    catalog = json.loads((ROOT / "docs/data-model/catalog.json").read_text())
    failures = (
        audit_definitions() + audit_sources(ROOT) + audit_catalog(ROOT) + audit_database(connection)
    )
    inventory = [
        {
            "table": name,
            "comment": inspector.get_table_comment(name)["text"],
            "columns": [
                {"name": col["name"], "type": str(col["type"]), "comment": col["comment"]}
                for col in inspector.get_columns(name)
            ],
        }
        for name in sorted(tables)
    ]
    write_json(
        output / "schema.json",
        {
            "passed": not failures,
            "failures": failures,
            "tables": len(tables),
            "columns": sum(len(row["columns"]) for row in inventory),
            "inventory": inventory,
            "design_only": [t["name"] for t in catalog["tables"] if t["status"] == "设计基线"],
            "boundary": "逐表比较实际 schema、登记模型与归档；设计对象和撤销领域表不要求建库。",
        },
    )


def fault_evidence(output):
    collected = [
        line
        for line in (output / "logs/collection.txt").read_text().splitlines()
        if line.startswith("tests/") and "::" in line
    ]
    observed = results(output)
    faults = []
    for name, nodes in FAULTS.items():
        status, evidence = classify(nodes, observed, collected)
        faults.append({"fault": name, "tests": nodes, "status": status, "evidence": evidence})
    write_json(output / "faults.json", faults)
    lines = [
        "# 26 故障注入记录",
        "",
        "通过独立 schema、Redis 前缀和受控响应注入，复用正式业务服务。",
        "",
    ]
    for item in faults:
        lines.extend([f"- {item['fault']}：{item['status']}。", ""])
        lines.extend(f"  `{node}`" for node in item["tests"])
        lines.append("")
    (output / "faults.md").write_text("\n".join(lines))
    return faults


def junit_outcomes(output):
    outcomes = {}
    for path in sorted((output / "logs").glob("*.xml"), key=lambda p: p.stat().st_mtime_ns):
        for case in ET.parse(path).getroot().iter("testcase"):
            name = f"{case.get('classname')}::{case.get('name')}"
            status = (
                "failed"
                if case.find("failure") is not None or case.find("error") is not None
                else "skipped"
                if case.find("skipped") is not None
                else "passed"
            )
            outcomes[name] = {"status": status, "evidence": str(path.relative_to(output))}
    return outcomes


def release_gate(output, report, failures):
    faults = fault_evidence(output)
    if not (output / "providers.json").exists():
        providers = json.loads((ROOT / "docs/runtime-providers.json").read_text())
        providers["recorded_at"] = datetime.now(UTC).isoformat()
        providers["reason"] = "本轮仅运行模型替身；没有提供并验证两种真实模型供应商或协议组合。"
        write_json(output / "providers.json", providers)
    providers = json.loads((output / "providers.json").read_text())
    blockers = []
    verified = [
        c
        for c in providers["combinations"]
        if c["status"] == "VERIFIED" and c.get("provider") and c.get("model") and c.get("run_ids")
    ]
    if len({(c["provider"], c["protocol"]) for c in verified}) < 2:
        blockers.append("未提供两种真实供应商/协议组合的运行、能力、重试和价格用量证据。")
    pending = [
        row["id"]
        for row in report["requirements"]
        if row["status"] not in {"关联用例通过", "后续阶段"}
    ]
    if pending:
        blockers.append("需求证据仍阻断：" + "、".join(pending))
    required = [
        "logs/unit.xml",
        "logs/integration.xml",
        "logs/browser.xml",
        "logs/onboarding.xml",
        "schema.json",
        "capacity.json",
        "sync-window.json",
        "two-domains.json",
        "api/text-brief.json",
        "api/archive-answer.json",
        "evaluations/text_items.report.json",
        "evaluations/numeric_summary.report.json",
    ]
    missing = [name for name in required if not (output / name).is_file()]
    if missing:
        blockers.append("缺少必需记录：" + "、".join(missing))
    if (output / "schema.json").exists() and not json.loads((output / "schema.json").read_text())[
        "passed"
    ]:
        blockers.append("实际 schema 审查失败。")
    if any(f["status"] != "关联用例通过" for f in faults):
        blockers.append("故障注入矩阵存在未通过或缺记录项。")
    journal = output / "commands.jsonl"
    commands = {}
    if journal.exists():
        for line in journal.read_text().splitlines():
            row = json.loads(line)
            commands[row["name"]] = row
    for name in ("service-check", "web-check", "migrations", "storage"):
        if name not in commands or commands[name]["exit_code"] != 0:
            blockers.append(f"{name} 缺少成功的命令记录。")
    outcomes = junit_outcomes(output)
    failed_tests = [node for node, row in outcomes.items() if row["status"] == "failed"]
    if failed_tests:
        blockers.append(f"最近一次参数用例仍失败：{len(failed_tests)} 项，详见 test-results.json。")
    write_json(output / "test-results.json", outcomes)
    performance = output / "performance.json"
    if not performance.exists():
        blockers.append("缺少独立性能测量记录。")
    else:
        measured = json.loads(performance.read_text())
        for name in ("management_query", "admission_service"):
            if not measured[name]["passed"]:
                blockers.append(
                    f"{name} P95 为 {measured[name]['p95_ms']} 毫秒，"
                    f"超过 {measured[name]['target_ms']} 毫秒目标。"
                )
        if "admission_http_including_subject_review" not in measured:
            blockers.append("完整 HTTP 受理性能尚未测量。")
    blockers.append("真实模型执行并发、供应商等待时延及同候选真实评测尚未验收。")
    gate = {
        "recorded_at": datetime.now(UTC).isoformat(),
        "formal_cutover_allowed": not blockers and not failures,
        "blockers": blockers,
        "failed_commands": failures,
        "test_summary": dict(Counter(row["status"] for row in outcomes.values())),
        "merge_rule": "相同测试及参数以最新 JUnit 覆盖；旧失败日志保留以追踪修复。",
    }
    write_json(output / "release-gate.json", gate)
    lines = [
        "# 26 本轮执行结果",
        "",
        "正式切换：" + ("允许" if gate["formal_cutover_allowed"] else "阻断") + "。",
        "",
        "JUnit 合并结果：" + json.dumps(gate["test_summary"], ensure_ascii=False) + "。",
        "",
        *[f"- {reason}" for reason in blockers],
        "",
        "[需求追踪](traceability.md) · [环境清单](environment.json) · [故障注入](faults.md) · "
        "[模型矩阵](providers.json) · [原始结果](test-results.json)",
    ]
    (output / "run-summary.md").write_text("\n".join(lines) + "\n")
    return gate
