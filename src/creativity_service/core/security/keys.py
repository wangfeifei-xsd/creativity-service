"""范围化缓存键、对象路径与带签名分页游标。"""

import base64
import hashlib
import hmac
import json
from typing import Any

from creativity_service.core.context import Scope
from creativity_service.core.primitives import ServiceError, canonical_json, digest


def scoped_key(scope: Scope, namespace: str, parts: list[str], prefix: str = "creativity") -> str:
    if not namespace.isidentifier() or not prefix.isidentifier():
        raise ValueError("命名空间不合法")
    suffix = digest([scope.model_dump(), parts])
    return f"{prefix}:{namespace}:{scope.channel_id}:{scope.environment}:{suffix}"


def object_path(scope: Scope, artifact_id: str) -> str:
    if not artifact_id.replace("_", "").isalnum():
        raise ValueError("文件标识不合法")
    base = f"channels/{scope.channel_id}/{scope.environment}/{digest(scope.model_dump())}"
    return f"{base}/staging/{artifact_id}"


class CursorCodec:
    def __init__(self, secret: bytes) -> None:
        if len(secret) < 32:
            raise ValueError("游标签名密钥至少为三十二字节")
        self.secret = secret

    def encode(self, scope: Scope, query: dict[str, Any], position: list[str]) -> str:
        body = canonical_json(
            {"v": 1, "scope": scope.model_dump(), "query": digest(query), "position": position}
        )
        signature = hmac.digest(self.secret, body, hashlib.sha256)
        return base64.urlsafe_b64encode(body + signature).decode().rstrip("=")

    def decode(self, cursor: str, scope: Scope, query: dict[str, Any]) -> list[str]:
        try:
            if len(cursor) > 2048:
                raise ValueError
            raw = base64.urlsafe_b64decode(cursor + "=" * (-len(cursor) % 4))
            body, signature = raw[:-32], raw[-32:]
            if not hmac.compare_digest(signature, hmac.digest(self.secret, body, hashlib.sha256)):
                raise ValueError
            value = json.loads(body)
            if (
                value["v"] != 1
                or value["scope"] != scope.model_dump()
                or value["query"] != digest(query)
            ):
                raise ValueError
            position = value["position"]
            if not isinstance(position, list) or not all(
                isinstance(item, str) for item in position
            ):
                raise ValueError
            return position
        except (ValueError, KeyError, TypeError) as exc:
            raise ServiceError("CURSOR_INVALID", "分页游标无效，请刷新列表", 422) from exc
