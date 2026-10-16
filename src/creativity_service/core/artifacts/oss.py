"""阿里云 OSS 官方 SDK 适配，部署环境与渠道路径隔离，通用文件返回外网 URL，历史产物兼容私有读取。"""

import asyncio
from collections.abc import Callable
from typing import Any
from urllib.parse import quote, unquote, urlsplit

import alibabacloud_oss_v2 as oss

from creativity_service.core.config import Settings
from creativity_service.core.database import assert_external_io_allowed
from creativity_service.core.primitives import ServiceError


def _call[T](operation: Callable[..., T], *args: Any, **kwargs: Any) -> T:
    """还原 SDK 包装的服务错误，统一处理权限、缺失对象与写入冲突。"""
    try:
        return operation(*args, **kwargs)
    except oss.exceptions.OperationError as exc:
        error = exc.unwrap()
        if isinstance(error, oss.exceptions.ServiceError):
            raise error from exc
        raise


class OSSObjectStore:
    def __init__(self, settings: Settings) -> None:
        self.bucket = settings.oss_bucket
        self.prefix = f"{settings.oss_path_prefix}{settings.environment}/"
        self.public_origin = f"https://{self.bucket}.oss-{settings.oss_region}.aliyuncs.com"
        self.transport = oss.transport.RequestsHttpClient(
            connect_timeout=settings.health_timeout_seconds,
            readwrite_timeout=30,
        )
        self.client = oss.Client(
            oss.Config(
                endpoint=settings.oss_endpoint,
                region=settings.oss_region,
                signature_version="v4",
                credentials_provider=oss.credentials.StaticCredentialsProvider(
                    settings.oss_access_key_id.get_secret_value(),
                    settings.oss_access_key_secret.get_secret_value(),
                ),
                http_client=self.transport,
                retry_max_attempts=1,
            )
        )
        self.health_timeout = settings.health_timeout_seconds

    def object_key(self, key: str) -> str:
        """数据库保存渠道相对路径，桶内额外添加服务与部署环境前缀。"""
        parts = key.rstrip("/").split("/")
        if (
            len(parts) < 3
            or parts[0] != "channels"
            or any(part in {"", ".", ".."} for part in parts)
            or "\\" in key
            or any(ord(char) < 32 for char in key)
        ):
            raise ValueError("对象路径必须包含渠道且不能越过服务存储范围")
        return self.prefix + key

    async def put(
        self,
        key: str,
        data: bytes,
        content_type: str,
        *,
        forbid_overwrite: bool = False,
        public: bool = False,
    ) -> None:
        assert_external_io_allowed()
        await asyncio.to_thread(
            _call,
            self.client.put_object,
            oss.PutObjectRequest(
                bucket=self.bucket,
                key=self.object_key(key),
                body=data,
                content_type=content_type,
                content_length=len(data),
                acl="public-read" if public else "private",
                forbid_overwrite=forbid_overwrite,
            ),
        )

    async def put_public(self, key: str, data: bytes, content_type: str) -> str:
        """通用文件返回稳定外网 URL，唯一对象键禁止覆盖已有文件。"""
        await self.put(key, data, content_type, forbid_overwrite=True, public=True)
        return self.public_url(key)

    def public_url(self, key: str) -> str:
        return self.public_origin + "/" + quote(self.object_key(key), safe="/")

    def key_from_url(self, url: str) -> str:
        """只解析本服务对象地址，业务读取不向用户提供的任意主机发送请求。"""
        parsed = urlsplit(url)
        physical = unquote(parsed.path, errors="strict").removeprefix("/")
        if (
            parsed.scheme + "://" + parsed.netloc != self.public_origin
            or parsed.query
            or parsed.fragment
            or not physical.startswith(self.prefix)
        ):
            raise ValueError("文件地址不属于当前对象存储")
        key = physical.removeprefix(self.prefix)
        if self.public_url(key) != url:
            raise ValueError("文件地址格式不正确")
        return key

    async def get(self, key: str, max_bytes: int) -> bytes:
        assert_external_io_allowed()

        def read() -> bytes:
            response = _call(
                self.client.get_object,
                oss.GetObjectRequest(bucket=self.bucket, key=self.object_key(key)),
            )
            body = response.body
            try:
                if response.content_length is not None and response.content_length > max_bytes:
                    raise ServiceError("ARTIFACT_SIZE_INVALID", "文件大小超限", 422)
                data = bytearray()
                # 按块读取且二次校验大小，不能信任缺失或错误的响应长度。
                for chunk in body.iter_bytes(block_size=64 * 1024):
                    if len(data) + len(chunk) > max_bytes:
                        raise ServiceError("ARTIFACT_SIZE_INVALID", "文件大小超限", 422)
                    data.extend(chunk)
                return bytes(data)
            finally:
                body.close()

        return await asyncio.to_thread(read)

    def check(self) -> None:
        """只读探测本环境前缀，不创建桶或修改桶策略。"""
        assert_external_io_allowed()
        _call(
            self.client.list_objects_v2,
            oss.ListObjectsV2Request(
                bucket=self.bucket, prefix=self.prefix + "channels/", max_keys=1
            ),
            readwrite_timeout=self.health_timeout,
        )

    def close(self) -> None:
        self.transport.close()
