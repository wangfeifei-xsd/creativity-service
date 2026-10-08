"""离线导出共享 schema、交接样例和兼容版本。"""

import argparse
import json
from pathlib import Path
from typing import Any

from creativity_service.core.context import AuthContext, Scope
from creativity_service.core.contracts import CONTRACTS
from creativity_service.core.primitives import digest

CONTRACT_VERSION = "1.2.0"


def schemas(ref_template: str = "#/$defs/{model}") -> dict[str, Any]:
    result = {}
    for contract in CONTRACTS:
        schema = contract.model_json_schema(ref_template=ref_template, mode="serialization")
        result.update(schema.pop("$defs", {}))
        result[contract.__name__] = schema
    return result


def examples() -> dict[str, dict[str, Any]]:
    scope = Scope(
        channel_id="channel_demo",
        environment="test",
        subject_type="member",
        subject_id="subject_demo",
    ).model_dump(mode="json")
    now, expiry = "2026-10-01T00:00:00Z", "2026-10-02T00:00:00Z"
    context = AuthContext(
        scope=Scope(**scope),
        principal_type="service",
        principal_id="client_demo",
        request_id="request_demo",
        client_id="client_demo",
        key_id="key_demo",
    ).model_dump(mode="json")
    version: dict[str, Any] = dict(
        channel_id="channel_demo",
        resource_type="agent",
        resource_id="agent_demo",
        version_id="version_demo",
        version_label="第一版",
        state="PUBLISHED",
        draft_revision=None,
        content={"entrypoint": "structured"},
        content_digest=digest(
            {"content": {"entrypoint": "structured"}, "output_schema": {"type": "object"}}
        ),
        dependency_version_ids=[],
        dependencies_digest=digest([]),
        output_schema={"type": "object"},
    )
    evidence = dict(
        evidence_id="evidence_demo",
        scope=scope,
        source_type="tool",
        source_id="source_demo",
        source_version="1",
        observed_at=now,
        location={"field_path": ["price"], "text_start": None, "text_end": None},
        title="业务报价",
        authorized_actions=["evidence:read"],
    )
    status = dict(value="PUBLISHED", label="已发布", tone="success")
    artifact = dict(
        artifact_id="artifact_demo",
        scope=scope,
        name="分析报告.csv",
        content_type="text/csv",
        size_bytes=128,
        sha256="a" * 64,
        state="AVAILABLE",
        expires_at=expiry,
        download_path="/api/v1/artifacts/artifact_demo/content",
    )
    success = {
        "IdentitySource": dict(
            scope=scope,
            source_type="service",
            principal_id="client_demo",
            actor_id=None,
            client_id="client_demo",
            key_id="key_demo",
        ),
        "Scope": scope,
        "ControlScope": dict(
            channel_id="system", purpose="identity_lookup", actor_id="auth_service"
        ),
        "AuthContext": {**context, "session_id": "session_demo", "token_digest": "a" * 64},
        "ControlAuthContext": dict(
            scope={
                "channel_id": "system",
                "purpose": "channel_directory",
                "actor_id": "admin_demo",
            },
            principal_type="management",
            principal_id="admin_demo",
            request_id="request_demo",
            session_id="session_demo",
            token_digest="a" * 64,
            granted_actions=[],
        ),
        "ChannelState": dict(
            channel_id="channel_demo",
            environment="test",
            channel_active=True,
            environment_active=True,
            membership_active=None,
            client_active=True,
            key_active=True,
        ),
        "ResourceVersion": version,
        "ReleaseSnapshot": dict(
            snapshot_id="snapshot_demo",
            scope=scope,
            run_id="run_demo",
            purpose="production",
            versions=[version],
            dependencies_digest=digest([version]),
            output_schema={"type": "object"},
            captured_at=now,
        ),
        "Admission": dict(
            admission_id="admission_demo",
            scope=scope,
            run_id="run_demo",
            policy_ids=["policy_demo"],
            state="HELD",
            expires_at=expiry,
        ),
        "BudgetReservation": dict(
            reservation_id="reservation_demo",
            scope=scope,
            run_id="run_demo",
            attempt_id="attempt_demo",
            policy_id="policy_demo",
            reserved={"amount": "0.50000000", "currency": "CNY"},
            token_limit=1000,
            state="HELD",
            expires_at=expiry,
        ),
        "Attempt": dict(
            scope=scope,
            attempt_id="attempt_demo",
            run_id="run_demo",
            step_id="step_demo",
            kind="model",
            target_version_id="version_demo",
            source_request_id="provider_request",
            state="SUCCEEDED",
            started_at=now,
            finished_at=now,
            error=None,
        ),
        "UsageEvent": dict(
            scope=scope,
            attempt_id="attempt_demo",
            connection_id="connection_demo",
            source_request_id="provider_request",
            event_version=2,
            status="REPORTED",
            raw_usage={"input_tokens": 120, "cached_tokens": 20},
            normalized_tokens={"input": 120, "cached": 20},
            subset_relations={"cached": "input"},
            cumulative=True,
            final=True,
            observed_at=now,
        ),
        "EvidenceRef": evidence,
        "ToolResult": dict(
            scope=scope,
            tool_version_id="tool_version_demo",
            source_request_id="source_request",
            source_version="1",
            observed_at=now,
            data={"price": "25.00", "currency": "CNY"},
            evidence_refs=[evidence],
            warnings=[],
            cursor=None,
            has_more=False,
            truncated=False,
            coverage={"items": 1, "exhaustive": True},
        ),
        "ResultEnvelope": dict(
            channel_id="channel_demo",
            run_id="run_demo",
            state="SUCCEEDED",
            state_label="已完成",
            release_snapshot_id="snapshot_demo",
            result={
                "schema_version": "1.0",
                "business_status": "COMPLETED",
                "data": {"summary": "分析完成"},
                "warnings": [],
                "evidence_refs": [evidence],
            },
            partial_output=None,
            error=None,
            usage_summary={"status": "REPORTED"},
            artifacts=[artifact],
        ),
        "RunEvent": dict(
            scope=scope,
            event_id="event_demo",
            run_id="run_demo",
            sequence=1,
            event_type="accepted",
            payload={"state": "QUEUED"},
            occurred_at=now,
            expires_at=expiry,
        ),
        "EventCursorExpired": dict(
            code="EVENTS_EXPIRED",
            message="事件已过期，请查询运行结果",
            run_id="run_demo",
            snapshot_path="/api/v1/runs/run_demo",
        ),
        "Artifact": artifact,
        "DeletionGuardResult": dict(
            scope=scope,
            target_type="artifact",
            target_id="artifact_demo",
            operation="read",
            checked_at=now,
            recovery_id="recovery_demo",
            allowed=True,
        ),
        "DisplayStatus": status,
        "VersionOption": dict(
            version_id="version_demo",
            resource_name="经营分析",
            version_label="第一版",
            status=status,
            selectable=True,
            unavailable_reason=None,
        ),
        "NavigationItem": dict(navigation_key="agents", label="智能体"),
        "VisibleAction": dict(action_key="release", label="发布"),
    }
    result = {}
    for contract in CONTRACTS:
        sample = contract.model_validate(success[contract.__name__]).model_dump(mode="json")
        result[contract.__name__] = {
            "contract_version": CONTRACT_VERSION,
            "success": sample,
            "missing": {
                "error": {
                    "code": "DEPENDENCY_UNAVAILABLE",
                    "message": "必要对象或服务不可用",
                    "fields": [],
                },
                "request_id": "request_demo",
            },
            "failure": {
                "error": {"code": "FORBIDDEN", "message": "无权执行此操作", "fields": []},
                "request_id": "request_demo",
            },
        }
    return result


def outputs() -> dict[Path, str]:
    contracts = {}
    for contract in CONTRACTS:
        schema = contract.model_json_schema(mode="serialization")
        schema["$schema"] = "https://json-schema.org/draft/2020-12/schema"
        schema["$id"] = f"urn:creativity:core:{CONTRACT_VERSION}:{contract.__name__}"
        contracts[contract.__name__] = schema
    return {
        path: json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
        for path, value in {
            Path("contracts/internal/core.json"): contracts,
            Path("contracts/examples.json"): examples(),
        }.items()
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="导出公共交接契约")
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    for path, content in outputs().items():
        if args.check:
            if not path.exists() or path.read_text() != content:
                raise SystemExit(f"契约过期：{path}")
        else:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content)


if __name__ == "__main__":
    main()
