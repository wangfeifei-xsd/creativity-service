"""业务后端与平台共用的版本化委托签名协议。"""

import base64
import hashlib
import hmac
import json
import re
from typing import Annotated, Any, Literal

from pydantic import Field, ValidationError

from creativity_service.core.context import Environment
from creativity_service.core.primitives import Contract, Digest, Identifier, ServiceError

HEADER = "X-Business-Delegation"
PROTOCOL = "business-delegation-v1"
MAX_ENVELOPE = 16384
Action = Annotated[str, Field(pattern=r"^[a-z_]+:[a-z_]+$", max_length=64)]


class SourceScope(Contract):
    type: str = Field(min_length=1, max_length=64)
    id: str = Field(min_length=1, max_length=128)


class RequestBinding(Contract):
    method: Literal["GET", "POST", "PUT", "PATCH", "DELETE", "HEAD"]
    target: str = Field(min_length=1, max_length=4096, pattern=r"^/api/v1/")
    body_sha256: Digest
    idempotency_key: str | None = Field(default=None, min_length=1, max_length=128)


class DelegationClaims(Contract):
    subject_type: Identifier
    subject_id: Identifier
    data_scope: SourceScope
    actions: list[Action] = Field(min_length=1, max_length=64)
    resources: dict[Identifier, list[Identifier | Literal["*"]]]
    issuer: str = Field(min_length=1, max_length=128)
    audience: str = Field(min_length=1, max_length=128)
    issued_at: int = Field(strict=True, ge=0, le=9007199254740991)
    expires_at: int = Field(strict=True, ge=0, le=9007199254740991)
    nonce: str = Field(pattern=r"^[A-Za-z0-9_-]{16,128}$")
    request: RequestBinding
    channel_id: Identifier | None = None
    environment: Environment | None = None


def b64url(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).decode("ascii").rstrip("=")


def decode_segment(value: str) -> bytes:
    if not value or not re.fullmatch(r"[A-Za-z0-9_-]+", value):
        raise ValueError("委托编码不正确")
    data = base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))
    if b64url(data) != value:
        raise ValueError("委托编码不规范")
    return data


def signing_string(kid: str, payload: str) -> bytes:
    # 签名覆盖原始载荷段，验签端不得重新序列化 JSON；避免跨语言数值与转义差异。
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", kid):
        raise ValueError("委托密钥编号不正确")
    return f"{PROTOCOL}\n{kid}\n{payload}".encode("ascii")


def sign(claims: DelegationClaims, kid: str, secret: bytes) -> str:
    if len(secret) < 32:
        raise ValueError("委托密钥至少为三十二字节")
    payload = b64url(claims.model_dump_json(exclude_none=True).encode("utf-8"))
    signature = b64url(hmac.digest(secret, signing_string(kid, payload), "sha256"))
    return f"v1.{kid}.{payload}.{signature}"


def split_envelope(envelope: str) -> tuple[str, str, bytes]:
    try:
        if len(envelope) > MAX_ENVELOPE:
            raise ValueError
        version, kid, payload, signature = envelope.split(".")
        if version != "v1" or len(decode_segment(signature)) != 32:
            raise ValueError
        signing_string(kid, payload)
        decode_segment(payload)
        return kid, payload, decode_segment(signature)
    except (ValueError, UnicodeError):
        raise ServiceError("DELEGATION_INVALID", "业务主体委托无效", 401) from None


def unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("委托载荷含重复字段")
        result[key] = value
    return result


def verify_payload(kid: str, payload: str, signature: bytes, secret: bytes) -> DelegationClaims:
    expected = hmac.digest(secret, signing_string(kid, payload), "sha256")
    if not hmac.compare_digest(signature, expected):
        raise ServiceError("DELEGATION_INVALID", "业务主体委托无效", 401)
    try:
        raw = json.loads(decode_segment(payload), object_pairs_hook=unique_object)
        return DelegationClaims.model_validate(raw)
    except (ValueError, ValidationError, UnicodeError, RecursionError):
        raise ServiceError("DELEGATION_INVALID", "业务主体委托无效", 401) from None


def bind_request(
    method: str, target: str, body: bytes, idempotency_key: str | None = None
) -> RequestBinding:
    return RequestBinding.model_validate(
        {
            "method": method,
            "target": target,
            "body_sha256": hashlib.sha256(body).hexdigest(),
            "idempotency_key": idempotency_key,
        }
    )
