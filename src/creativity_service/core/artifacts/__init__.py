"""受控文件传输与暂存回收，不向客户端签发绕过实时授权的对象地址。"""

from __future__ import annotations

import asyncio
import hashlib
import re
from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from mypy_boto3_s3 import S3Client
    from mypy_boto3_s3.type_defs import ListObjectsV2RequestTypeDef
from sqlalchemy.ext.asyncio import AsyncEngine

from creativity_service.core.context import AuthContext, Authorization, DenyAuthorization
from creativity_service.core.contracts import Artifact
from creativity_service.core.database import Repository, assert_external_io_allowed, transaction
from creativity_service.core.database.tables import metadata
from creativity_service.core.deletion import ContentRef, DeletionGuard, content_key
from creativity_service.core.locking import record_key
from creativity_service.core.primitives import ServiceError, new_id, utcnow
from creativity_service.core.security.keys import object_path

MAX_FILE_BYTES = 20 * 1024 * 1024


class ObjectStore(Protocol):
    async def put(self, key: str, data: bytes, content_type: str) -> None: ...
    async def get(self, key: str, max_bytes: int) -> bytes: ...
    async def delete(self, key: str) -> None: ...


class S3ObjectStore:
    def __init__(self, client: S3Client, bucket: str) -> None:
        self.client, self.bucket = client, bucket

    async def put(self, key: str, data: bytes, content_type: str) -> None:
        assert_external_io_allowed()
        await asyncio.to_thread(
            self.client.put_object, Bucket=self.bucket, Key=key, Body=data, ContentType=content_type
        )

    async def get(self, key: str, max_bytes: int) -> bytes:
        assert_external_io_allowed()

        def read() -> bytes:
            response = self.client.get_object(Bucket=self.bucket, Key=key)
            body = response["Body"]
            try:
                if response["ContentLength"] > max_bytes:
                    raise ServiceError("ARTIFACT_SIZE_INVALID", "文件大小超限", 422)
                data = body.read(max_bytes + 1)
                if len(data) > max_bytes:
                    raise ServiceError("ARTIFACT_SIZE_INVALID", "文件大小超限", 422)
                return data
            finally:
                body.close()

        return await asyncio.to_thread(read)

    async def delete(self, key: str) -> None:
        assert_external_io_allowed()
        await asyncio.to_thread(self.client.delete_object, Bucket=self.bucket, Key=key)

    async def list_page(
        self, prefix: str, cursor: str | None = None
    ) -> tuple[list[tuple[str, datetime]], str | None]:
        assert_external_io_allowed()

        def read() -> tuple[list[tuple[str, datetime]], str | None]:
            request: ListObjectsV2RequestTypeDef = {
                "Bucket": self.bucket,
                "Prefix": prefix,
                "MaxKeys": 1000,
            }
            if cursor:
                request["ContinuationToken"] = cursor
            response = self.client.list_objects_v2(**request)
            return [
                (item["Key"], item["LastModified"])
                for item in response.get("Contents", [])
                if "Key" in item and "LastModified" in item
            ], response.get("NextContinuationToken")

        return await asyncio.to_thread(read)


class ArtifactService:
    def __init__(
        self, engine: AsyncEngine, store: ObjectStore, authorization: Authorization | None = None
    ) -> None:
        self.engine, self.store = engine, store
        self.authorization = authorization or DenyAuthorization()

    async def upload(
        self,
        context: AuthContext,
        name: str,
        content_type: str,
        data: bytes,
        sources: list[ContentRef],
        retention_seconds: int = 30 * 86400,
    ) -> Artifact:
        assert_external_io_allowed()
        await self.authorization.require(context, "artifact:upload", "new")
        if (
            not name
            or len(name) > 255
            or any(ord(c) < 32 for c in name)
            or len(data) > MAX_FILE_BYTES
            or retention_seconds < 1
            or not sources
            or not re.fullmatch(r"[a-zA-Z0-9.+-]+/[a-zA-Z0-9.+-]+", content_type)
        ):
            raise ServiceError("ARTIFACT_INVALID", "请检查文件名称、类型、来源和大小", 422)
        scope, artifact_id = context.scope, new_id("artifact")
        for source in sources:
            await self.authorization.require(context, "content:derive", source.resource_id)
        link_ids = [new_id("source") for _ in sources]
        repo = Repository(metadata.tables["artifacts"], scope)
        key = object_path(scope, artifact_id)
        ref = ContentRef("artifact", artifact_id)
        keys = [content_key(scope), record_key(scope.channel_id, "artifacts", artifact_id)]
        keys += [record_key(scope.channel_id, "source_links", link_id) for link_id in link_ids]
        async with transaction(self.engine, scope, keys) as uow:
            guard = DeletionGuard(scope)
            await guard.check(uow, [*sources, ref])
            row = await repo.add(
                uow,
                artifact_id,
                {
                    "name": name,
                    "content_type": content_type,
                    "object_key": key,
                    "size_bytes": len(data),
                    "sha256": hashlib.sha256(data).hexdigest(),
                    "state": "STAGED",
                    "expires_at": utcnow() + timedelta(seconds=retention_seconds),
                    "upload_expires_at": utcnow() + timedelta(hours=1),
                    "registered_at": None,
                },
            )
            for link_id, source in zip(link_ids, sources, strict=True):
                await guard.link(uow, link_id, source, ref)
        # 对象访问不占用数据库事务；失败保持暂存意图供重试清理。
        try:
            await self.store.put(key, data, content_type)
            await self.authorization.require(context, "artifact:upload", artifact_id)
            async with transaction(self.engine, scope, keys) as uow:
                await DeletionGuard(scope).check(uow, [ref])
                current = await repo.get(uow.connection, artifact_id)
                if (
                    current is None
                    or current["state"] != "STAGED"
                    or current["upload_expires_at"] <= utcnow()
                ):
                    raise ServiceError("UPLOAD_EXPIRED", "文件暂存已失效", 410)
                row = await repo.change(
                    uow,
                    artifact_id,
                    current["revision"],
                    {"state": "AVAILABLE", "registered_at": utcnow()},
                )
        except BaseException:
            # 删除与迟到上传竞争时立即回收；回收失败仍保留暂存记录供扫描重试。
            try:
                await self.store.delete(key)
            except Exception:
                pass
            raise
        api_prefix = "admin" if context.principal_type == "management" else "api"
        return Artifact(
            artifact_id=artifact_id,
            scope=scope,
            name=name,
            content_type=content_type,
            size_bytes=len(data),
            sha256=row["sha256"],
            state="AVAILABLE",
            expires_at=row["expires_at"],
            download_path=f"/{api_prefix}/v1/artifacts/{artifact_id}/content",
        )

    async def download(self, context: AuthContext, artifact_id: str) -> tuple[bytes, str, str]:
        assert_external_io_allowed()
        await self.authorization.require(context, "artifact:download", artifact_id)
        scope = context.scope
        repo = Repository(metadata.tables["artifacts"], scope)
        keys = [content_key(scope), record_key(scope.channel_id, "artifacts", artifact_id)]
        async with transaction(self.engine, scope, keys) as uow:
            await DeletionGuard(scope).check(uow, [ContentRef("artifact", artifact_id)])
            row = await repo.get(uow.connection, artifact_id)
            if row is None or row["state"] != "AVAILABLE" or row["expires_at"] <= utcnow():
                raise ServiceError("NOT_FOUND", "文件不存在或已失效", 404)
        data = await self.store.get(row["object_key"], MAX_FILE_BYTES)
        await self.authorization.require(context, "artifact:download", artifact_id)
        async with transaction(self.engine, scope, keys) as uow:
            await DeletionGuard(scope).check(uow, [ContentRef("artifact", artifact_id)])
            current = await repo.get(uow.connection, artifact_id)
            if (
                current is None
                or current["state"] != "AVAILABLE"
                or current["expires_at"] <= utcnow()
                or current["revision"] != row["revision"]
            ):
                raise ServiceError("NOT_FOUND", "文件不存在或已失效", 404)
        if len(data) != row["size_bytes"] or hashlib.sha256(data).hexdigest() != row["sha256"]:
            raise ServiceError("ARTIFACT_CORRUPT", "文件校验失败", 503)
        return data, row["content_type"], row["name"]

    async def clean(self, context: AuthContext, ref: ContentRef, *, orphan: bool = False) -> None:
        assert_external_io_allowed()
        await self.authorization.require(context, "artifact:cleanup", ref.resource_id)
        if ref.resource_type != "artifact":
            raise ValueError("文件清理只接受文件引用")
        scope = context.scope
        repo = Repository(metadata.tables["artifacts"], scope, include_deleted=True)
        keys = [content_key(scope), record_key(scope.channel_id, "artifacts", ref.resource_id)]
        async with transaction(self.engine, scope, keys) as uow:
            row = await repo.get(uow.connection, ref.resource_id)
            if row is None:
                return
            if orphan:
                if (
                    row["state"] not in {"STAGED", "DELETING", "DELETED"}
                    or row["upload_expires_at"] > utcnow()
                ):
                    raise ServiceError("UPLOAD_ACTIVE", "暂存文件尚未到清理时间")
            else:
                try:
                    await DeletionGuard(scope).check(uow, [ref])
                except ServiceError as exc:
                    if exc.code != "CONTENT_DELETED":
                        raise
                else:
                    raise ServiceError("DELETION_MARKER_REQUIRED", "清理前必须登记删除标记")
            if row["state"] != "DELETING":
                row = await repo.change(
                    uow, row["id"], row["revision"], {"state": "DELETING", "is_deleted": True}
                )
        await self.store.delete(row["object_key"])
        async with transaction(self.engine, scope, keys) as uow:
            current = await repo.get(uow.connection, ref.resource_id)
            if current and current["state"] == "DELETING":
                await repo.change(
                    uow,
                    current["id"],
                    current["revision"],
                    {"is_deleted": True, "state": "DELETED", "name": "已删除文件"},
                )

    async def cleanup_orphans(self, context: AuthContext) -> int:
        assert_external_io_allowed()
        await self.authorization.require(context, "artifact:cleanup", "scope")
        repo = Repository(metadata.tables["artifacts"], context.scope, include_deleted=True)
        async with self.engine.connect() as connection:
            rows = await repo.find(connection)
        cleaned = 0
        for row in rows:
            if (
                row["state"] in {"STAGED", "DELETING", "DELETED"}
                and row["upload_expires_at"] <= utcnow()
            ):
                # 已删暂存项也重复清理，以覆盖上次清理后才结束的失败上传。
                await self.clean(context, ContentRef("artifact", row["id"]), orphan=True)
                cleaned += 1
        return cleaned
