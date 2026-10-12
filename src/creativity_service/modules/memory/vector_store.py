"""Qdrant REST 适配：完整主体范围内检索，网络请求均在短事务外。"""

import json
from typing import Any
from uuid import NAMESPACE_URL, uuid5

import httpx

from creativity_service.core.config import Settings
from creativity_service.core.context import Scope
from creativity_service.core.database import assert_external_io_allowed
from creativity_service.core.primitives import ServiceError

# 渠道使用租户索引，其余字段用于同一请求内的严格过滤。
PAYLOAD_INDEXES = {
    "channel_id": {"type": "keyword", "is_tenant": True},
    **{
        field: "keyword"
        for field in ("environment", "subject_type", "subject_id", "model_version_id", "id")
    },
}


class QdrantStore:
    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or Settings()

    def collection(self, dimensions: int) -> str:
        if not 1 <= dimensions <= 4096:
            raise ValueError("向量维度无效")
        return f"{self.settings.qdrant_collection_prefix}_d{dimensions}"

    async def request(
        self, method: str, path: str, *, missing_ok: bool = False, **payload: Any
    ) -> Any:
        assert_external_io_allowed()
        api_key = self.settings.qdrant_api_key.get_secret_value()
        try:
            async with httpx.AsyncClient(
                base_url=self.settings.qdrant_uri,
                timeout=self.settings.qdrant_timeout_seconds,
                trust_env=False,
                headers={"api-key": api_key} if api_key else {},
            ) as client:
                response = await client.request(method, path, json=payload if payload else None)
                if missing_ok and response.status_code == 404:
                    return None
                response.raise_for_status()
                body = response.json()
                if not isinstance(body, dict) or body.get("status") != "ok" or "result" not in body:
                    raise ValueError("向量服务返回失败")
                return body["result"]
        except (httpx.HTTPError, ValueError, TypeError) as exc:
            raise ServiceError("VECTOR_UNAVAILABLE", "Qdrant 向量服务暂不可用", 503) from exc

    async def health(self) -> None:
        result = await self.request("GET", "/collections")
        if not isinstance(result, dict) or not isinstance(result.get("collections"), list):
            raise ServiceError("VECTOR_UNAVAILABLE", "Qdrant 连接验证失败", 503)

    async def ensure_collection(self, dimensions: int) -> None:
        path = f"/collections/{self.collection(dimensions)}"
        info = await self.request("GET", path, missing_ok=True)
        if info is None:
            try:
                await self.request("PUT", path, vectors={"size": dimensions, "distance": "Cosine"})
            except ServiceError:
                # 并行进程可能已完成创建，必须重新核对集合配置才能接受竞争结果。
                info = await self.request("GET", path, missing_ok=True)
                if info is None:
                    raise
            else:
                info = await self.request("GET", path)
        vectors = info.get("config", {}).get("params", {}).get("vectors", {})
        if vectors.get("size") != dimensions or vectors.get("distance") != "Cosine":
            raise ServiceError("VECTOR_UNAVAILABLE", "Qdrant 集合维度或距离配置不匹配", 503)
        indexes = info.get("payload_schema", {})
        for field, schema in PAYLOAD_INDEXES.items():
            existing = indexes.get(field)
            if existing is not None:
                if existing.get("data_type") != "keyword" or (
                    field == "channel_id" and not existing.get("params", {}).get("is_tenant")
                ):
                    raise ServiceError("VECTOR_UNAVAILABLE", "Qdrant 集合过滤索引配置不匹配", 503)
                continue
            await self.request(
                "PUT", path + "/index?wait=true", field_name=field, field_schema=schema
            )

    @staticmethod
    def scope_filter(scope: Scope) -> dict[str, Any]:
        if (
            not scope.channel_id
            or not scope.environment
            or not scope.subject_type
            or not scope.subject_id
        ):
            raise ValueError("向量操作必须包含完整渠道、环境和主体")
        return {
            "must": [
                {"key": key, "match": {"value": value}} for key, value in scope.model_dump().items()
            ]
        }

    @staticmethod
    def point_id(scope: Scope, identifier: str) -> str:
        # UUID 同时包含完整主体范围；同编号跨渠道写入不能覆盖其他主体的点。
        key = json.dumps([scope.model_dump(), identifier], sort_keys=True, ensure_ascii=False)
        return str(uuid5(NAMESPACE_URL, "creativity-memory:" + key))

    @staticmethod
    def require_completed(result: Any) -> None:
        if not isinstance(result, dict) or result.get("status") != "completed":
            raise ServiceError("VECTOR_UNAVAILABLE", "Qdrant 尚未确认向量操作完成", 503)

    async def upsert(self, scope: Scope, dimensions: int, rows: list[dict[str, Any]]) -> None:
        self.scope_filter(scope)
        if not rows:
            return
        points = []
        for row in rows:
            if any(row[key] != value for key, value in scope.model_dump().items()):
                raise ValueError("向量记录与受信范围不符")
            if len(row["embedding"]) != dimensions:
                raise ValueError("向量记录维度不一致")
            points.append(
                {
                    "id": self.point_id(scope, row["id"]),
                    "vector": row["embedding"],
                    "payload": {
                        key: row[key]
                        for key in (
                            "id",
                            "channel_id",
                            "environment",
                            "subject_type",
                            "subject_id",
                            "memory_id",
                            "memory_version_id",
                            "model_version_id",
                        )
                    },
                }
            )
        await self.ensure_collection(dimensions)
        result = await self.request(
            "PUT",
            f"/collections/{self.collection(dimensions)}/points?wait=true&ordering=strong",
            points=points,
        )
        self.require_completed(result)

    async def delete(self, scope: Scope, dimensions: int, ids: list[str]) -> None:
        expression = self.scope_filter(scope)
        path = f"/collections/{self.collection(dimensions)}"
        if not ids or await self.request("GET", path, missing_ok=True) is None:
            return
        expression["must"].append({"key": "id", "match": {"any": ids}})
        result = await self.request(
            "POST", path + "/points/delete?wait=true&ordering=strong", filter=expression
        )
        self.require_completed(result)

    async def search(
        self, scope: Scope, model: str, query: list[float], ids: list[str], limit: int
    ) -> list[str]:
        expression = self.scope_filter(scope)
        if not ids:
            return []
        expression["must"].extend(
            [
                {"key": "model_version_id", "match": {"value": model}},
                {"key": "id", "match": {"any": ids}},
            ]
        )
        found = await self.request(
            "POST",
            f"/collections/{self.collection(len(query))}/points/query?consistency=all",
            query=query,
            filter=expression,
            limit=limit,
            with_payload=["id"],
            with_vector=False,
        )
        try:
            identifiers = [row["payload"]["id"] for row in found["points"]]
            if any(
                not isinstance(identifier, str) or identifier not in ids
                for identifier in identifiers
            ):
                raise ValueError("向量引用不在当前有效范围")
            return identifiers
        except (KeyError, TypeError, ValueError) as exc:
            raise ServiceError("VECTOR_UNAVAILABLE", "Qdrant 返回的向量引用无效", 503) from exc
