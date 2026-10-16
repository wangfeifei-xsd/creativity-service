"""通用文件上传只生成稳定 URL，与业务记录及其删除生命周期无关。"""

import re
from urllib.parse import unquote, urlsplit

from pydantic import Field

from creativity_service.core.artifacts import MAX_FILE_BYTES, ObjectStore
from creativity_service.core.context import AuthContext, Authorization
from creativity_service.core.database import assert_external_io_allowed
from creativity_service.core.primitives import Contract, ServiceError, new_id


class UploadedFile(Contract):
    url: str = Field(description="文件的稳定 OSS 外网 URL")
    name: str
    content_type: str
    size_bytes: int


def file_name(url: str) -> str:
    return unquote(urlsplit(url).path.rsplit("/", 1)[-1])


class FileService:
    def __init__(self, store: ObjectStore, authorization: Authorization) -> None:
        self.store, self.authorization = store, authorization

    async def upload(
        self, context: AuthContext, name: str, content_type: str, data: bytes
    ) -> UploadedFile:
        assert_external_io_allowed()
        await self.authorization.require(context, "artifact:upload", "new")
        name = name.replace("\\", "/").rsplit("/", 1)[-1].strip()
        if (
            not name
            or name in {".", ".."}
            or len(name) > 255
            or any(ord(c) < 32 or ord(c) == 127 for c in name)
            or not data
            or len(data) > MAX_FILE_BYTES
            or not re.fullmatch(r"[a-zA-Z0-9.+-]+/[a-zA-Z0-9.+-]+", content_type)
        ):
            raise ServiceError("FILE_INVALID", "请检查文件名称、类型和大小（最多 20 MiB）", 422)
        # 渠道只取受信上下文；随机目录保证同名文件不会互相覆盖。
        key = f"channels/{context.scope.channel_id}/files/{new_id('file')}/{name}"
        url = await self.store.put_public(key, data, content_type)
        return UploadedFile(url=url, name=name, content_type=content_type, size_bytes=len(data))

    def key(self, context: AuthContext, url: str) -> str:
        try:
            key = self.store.key_from_url(url)
            if not key.startswith(f"channels/{context.scope.channel_id}/files/"):
                raise ValueError("文件不属于当前渠道")
            return key
        except (ValueError, UnicodeError) as exc:
            raise ServiceError("FILE_URL_INVALID", "请使用当前渠道上传的文件地址", 422) from exc

    async def read(self, context: AuthContext, url: str, max_bytes: int = MAX_FILE_BYTES) -> bytes:
        """业务服务完成授权后按本地解析的对象键读取，文件不依赖业务状态。"""
        assert_external_io_allowed()
        return await self.store.get(self.key(context, url), max_bytes)
