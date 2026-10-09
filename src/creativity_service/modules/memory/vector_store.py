"""Milvus REST v2 适配：仅接受完整主体范围，所有网络请求均在短事务外。"""

import json
from typing import Any

import httpx

from creativity_service.core.config import Settings
from creativity_service.core.context import Scope
from creativity_service.core.database import assert_external_io_allowed
from creativity_service.core.primitives import ServiceError


class MilvusStore:
    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or Settings()

    def collection(self, dimensions: int) -> str:
        if not 1 <= dimensions <= 4096:
            raise ValueError("向量维度无效")
        return f"{self.settings.milvus_collection_prefix}_d{dimensions}"

    async def request(self, operation: str, **payload: Any) -> Any:
        assert_external_io_allowed()
        token = self.settings.milvus_token.get_secret_value()
        try:
            async with httpx.AsyncClient(
                base_url=self.settings.milvus_uri,
                timeout=self.settings.milvus_timeout_seconds,
                trust_env=False,
                headers={"Authorization": f"Bearer {token}"} if token else {},
            ) as client:
                response = await client.post(
                    f"/v2/vectordb/{operation}",
                    json={"dbName": self.settings.milvus_database, **payload},
                )
                response.raise_for_status()
                body = response.json()
                if body.get("code") != 0:
                    raise ValueError("向量服务返回失败")
                return body.get("data")
        except (httpx.HTTPError, ValueError, TypeError) as exc:
            raise ServiceError("VECTOR_UNAVAILABLE", "Milvus 向量服务暂不可用", 503) from exc

    async def health(self) -> None:
        await self.request("collections/list")

    async def ensure_collection(self, dimensions: int) -> None:
        name = self.collection(dimensions)
        if (await self.request("collections/has", collectionName=name))["has"]:
            return
        fields = [
            {
                "fieldName": field,
                "dataType": "VarChar",
                "elementTypeParams": {"max_length": size},
                **({"isPrimary": True} if field == "id" else {}),
            }
            for field, size in (
                ("id", 64),
                ("channel_id", 64),
                ("environment", 16),
                ("subject_type", 64),
                ("subject_id", 128),
                ("memory_id", 64),
                ("memory_version_id", 64),
                ("model_version_id", 64),
            )
        ]
        fields.append(
            {
                "fieldName": "embedding",
                "dataType": "FloatVector",
                "elementTypeParams": {"dim": dimensions},
            }
        )
        try:
            await self.request(
                "collections/create",
                collectionName=name,
                schema={"autoId": False, "enableDynamicField": False, "fields": fields},
                indexParams=[
                    {
                        "fieldName": "embedding",
                        "indexName": "embedding_cosine",
                        "metricType": "COSINE",
                        "params": {"index_type": "AUTOINDEX"},
                    }
                ],
                params={"consistencyLevel": "Strong"},
            )
        except ServiceError:
            # 并行进程可能已创建同维度集合，只有再次确认存在才接受竞争结果。
            if not (await self.request("collections/has", collectionName=name))["has"]:
                raise

    @staticmethod
    def scope_filter(scope: Scope) -> str:
        if (
            not scope.channel_id
            or not scope.environment
            or not scope.subject_type
            or not scope.subject_id
        ):
            raise ValueError("向量操作必须包含完整渠道、环境和主体")
        return " and ".join(
            f"{key} == {json.dumps(value, ensure_ascii=False)}"
            for key, value in scope.model_dump().items()
        )

    async def upsert(self, scope: Scope, dimensions: int, rows: list[dict[str, Any]]) -> None:
        self.scope_filter(scope)
        if not rows:
            return
        await self.ensure_collection(dimensions)
        data = []
        for row in rows:
            if any(row[key] != value for key, value in scope.model_dump().items()):
                raise ValueError("向量记录与受信范围不符")
            data.append(
                {
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
                        "embedding",
                    )
                }
            )
        await self.request("entities/upsert", collectionName=self.collection(dimensions), data=data)

    async def delete(self, scope: Scope, dimensions: int, ids: list[str]) -> None:
        expression = self.scope_filter(scope)
        name = self.collection(dimensions)
        if not ids or not (await self.request("collections/has", collectionName=name))["has"]:
            return
        await self.request(
            "entities/delete",
            collectionName=name,
            filter=f"({expression}) and id in {json.dumps(ids)}",
        )

    async def search(
        self, scope: Scope, model: str, query: list[float], ids: list[str], limit: int
    ) -> list[str]:
        expression = self.scope_filter(scope)
        if not ids:
            return []
        found = await self.request(
            "entities/search",
            collectionName=self.collection(len(query)),
            data=[query],
            annsField="embedding",
            limit=limit,
            outputFields=["id"],
            consistencyLevel="Strong",
            filter=(
                f"({expression}) and model_version_id == {json.dumps(model)} "
                f"and id in {json.dumps(ids)}"
            ),
        )
        return [str(row["id"]) for row in found]
