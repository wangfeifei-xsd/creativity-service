"""导出版本化公共接入协议、标准 schema、错误表与公开签名向量。"""

import argparse
import json
from pathlib import Path
from typing import Any

from creativity_service.integrations.business.base import (
    ERRORS,
    BusinessEvidence,
    BusinessResult,
    Candidate,
    EntityRef,
    MetricDefinition,
    MetricResult,
    PolicyReference,
    RiskFact,
)
from creativity_service.integrations.business.delegation import (
    DelegationClaims,
    RequestBinding,
    bind_request,
    sign,
    signing_string,
    split_envelope,
)


def vectors() -> list[dict[str, Any]]:
    items = []
    for number, (method, target, body, idem) in enumerate(
        [
            (
                "POST",
                "/api/v1/runs",
                '{"agent_code":"match","input":{"request_text":"今晚推荐"},"delivery":"async"}',
                "order-20261002-1",
            ),
            ("GET", "/api/v1/runs/run-vector/events?after_sequence=2", "", None),
        ]
    ):
        claims = DelegationClaims(
            subject_type="MEMBER",
            subject_id="member-001",
            data_scope={"type": "club", "id": "club-001"},
            actions=["run:create", "run:read"],
            resources={"agent": ["agent-match"], "run": ["*"]},
            issuer="playmate-backend",
            audience="creativity-api",
            issued_at=1790899200,
            expires_at=1790899500,
            nonce=f"vector_nonce_0000000{number}",
            request=bind_request(method, target, body.encode(), idem),
            channel_id="channel-vector",
            environment="test",
        )
        secret, kid = bytes(range(32)), "dk_vector_2026"
        envelope = sign(claims, kid, secret)
        _, payload, _ = split_envelope(envelope)
        items.append(
            {
                "name": f"vector-{number + 1}",
                "kid": kid,
                "secret_hex": secret.hex(),
                "body": body,
                "claims": claims.model_dump(mode="json", exclude_none=True),
                "signing_string": signing_string(kid, payload).decode(),
                "envelope": envelope,
            }
        )
    return items


def artifacts() -> dict[str, object]:
    from creativity_service.app import create_schema_app

    app = create_schema_app()
    schema = app.openapi()
    schema["info"] = {"title": "身份委托与旧 HTTP 接入兼容", "version": "1.1.0"}
    schema["paths"] = {
        p: v
        for p, v in schema["paths"].items()
        if p.startswith(
            (
                "/admin/v1/integrations",
                "/admin/v1/delegation-keys",
                "/admin/v1/integration-credentials",
                "/admin/v1/subject-review-bindings",
                "/api/v1/auth/token",
                "/api/v1/runs",
            )
        )
    }
    all_definitions = schema["components"]["schemas"]
    retained: dict[str, Any] = {}

    def retain_refs(value: Any) -> None:
        if isinstance(value, dict):
            reference = value.get("$ref", "")
            if reference.startswith("#/components/schemas/"):
                name = reference.rsplit("/", 1)[1]
                if name not in retained:
                    retained[name] = all_definitions[name]
                    retain_refs(retained[name])
            for child in value.values():
                retain_refs(child)
        elif isinstance(value, list):
            for child in value:
                retain_refs(child)

    retain_refs(schema["paths"])
    schema["components"]["schemas"] = retained
    delegation_errors = {
        "DELEGATION_REQUIRED": ("缺少业务主体委托", 401),
        "DELEGATION_INVALID": ("签名、签发者、受众或声明格式无效", 401),
        "DELEGATION_EXPIRED": ("委托过期或时间不合法", 401),
        "DELEGATION_SCOPE_INVALID": ("委托归属或源数据域不符", 403),
        "DELEGATION_FORBIDDEN": ("委托超过当前权限或匿名范围", 403),
        "DELEGATION_REQUEST_MISMATCH": ("委托未绑定当前实际请求", 403),
        "DELEGATION_REPLAY": ("同一随机数已用于不同请求或身份", 409),
        "DELEGATION_KEY_CHANGED": ("验签期间密钥配置改变，请重试", 409),
        "DELEGATION_KEY_EXISTS": ("当前接入服务已有密钥，请轮换", 409),
        "DELEGATION_KEY_UNAVAILABLE": ("委托密钥不可轮换", 409),
        "DELEGATION_KEY_ROTATED": ("旧密钥已轮换，请使用新密钥", 409),
    }
    return {
        **{
            (
                "DelegationClaims-v1.1.schema.json"
                if model is DelegationClaims
                else f"{model.__name__}.schema.json"
            ): model.model_json_schema(mode="serialization")
            for model in (
                DelegationClaims,
                RequestBinding,
                EntityRef,
                BusinessEvidence,
                BusinessResult,
                Candidate,
                RiskFact,
                PolicyReference,
                MetricDefinition,
                MetricResult,
            )
        },
        "delegation-vectors.json": vectors(),
        "errors.json": {
            code: {"message": name, "http_status": status}
            for code, (name, status) in (ERRORS | delegation_errors).items()
        },
        "openapi-v1.1.json": schema,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="导出业务接入契约")
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    root = Path("contracts/integrations")
    if not args.check:
        root.mkdir(parents=True, exist_ok=True)
    for filename, value in artifacts().items():
        path = root / filename
        content = json.dumps(value, ensure_ascii=False, indent=2) + "\n"
        if args.check:
            if not path.exists() or path.read_text() != content:
                raise SystemExit(f"业务接入契约过期：{filename}")
        else:
            path.write_text(content)


if __name__ == "__main__":
    main()
