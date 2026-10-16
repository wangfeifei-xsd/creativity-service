"""通用上传返回可直接读取的 OSS URL，不创建业务附件或删除传播记录。"""

import httpx
import pytest

from creativity_service.app import create_schema_app
from creativity_service.core.artifacts import MAX_FILE_BYTES
from creativity_service.core.config import Settings
from creativity_service.core.database import Repository
from creativity_service.core.database.tables import metadata
from creativity_service.core.deletion import ContentRef, DeletionService
from creativity_service.core.infrastructure import Infrastructure
from creativity_service.core.primitives import ServiceError
from creativity_service.core.services import build_core_services
from tests.integration.conversations.conftest import Store

pytestmark = pytest.mark.integration


def application(engine, context, authorization, store):
    class Resolver:
        async def authenticate(self, bearer, purpose):
            if bearer != "valid":
                raise ServiceError("UNAUTHENTICATED", "请重新登录", 401)
            return context.model_copy(update={"session_id": "files_test", "token_digest": "a" * 64})

    app = create_schema_app()
    app.state.authentication = Resolver()
    app.state.core = build_core_services(engine, store, authorization)
    return app


async def test_real_upload_returns_stable_public_url_without_business_registration(
    engine, context, authorization
):
    infrastructure = Infrastructure(Settings())
    store = infrastructure.object_store
    app = application(engine, context, authorization, store)
    data = "通用上传测试".encode()
    try:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app), base_url="http://test"
        ) as client:
            response = await client.post(
                "/admin/v1/files/upload",
                files={"file": ("中文 空格#.txt", data, "text/plain")},
                headers={"Authorization": "Bearer valid"},
            )
        assert response.status_code == 201, response.text
        file = response.json()
        assert file["name"] == "中文 空格#.txt" and file["size_bytes"] == len(data)
        assert "-internal" not in file["url"] and "%23" in file["url"]
        async with engine.connect() as connection:
            for name in ("artifacts", "source_links"):
                assert not await Repository(metadata.tables[name], context.scope).find(connection)
        await DeletionService(engine, authorization).mark(
            context, ContentRef("conversation", "removed_business_record"), "USER_REQUEST"
        )
        async with httpx.AsyncClient(timeout=20) as public:
            downloaded = await public.get(file["url"])
        assert downloaded.status_code == 200 and downloaded.content == data
    finally:
        await infrastructure.close()


async def test_generic_upload_requires_auth_and_rejects_invalid_files(
    engine, context, authorization
):
    store = Store()
    app = application(engine, context, authorization, store)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app), base_url="http://test"
    ) as client:
        missing = await client.post("/admin/v1/files/upload", files={"file": ("x.txt", b"x")})
        assert missing.status_code == 401
        client.headers["Authorization"] = "Bearer valid"
        for data in (b"", b"x" * (MAX_FILE_BYTES + 1)):
            result = await client.post("/admin/v1/files/upload", files={"file": ("x.txt", data)})
            assert result.status_code == 422
        extra = await client.post(
            "/admin/v1/files/upload", files={"file": ("x.txt", b"x")}, data={"channel_id": "other"}
        )
        assert extra.status_code == 422
        authorization.denied = True
        denied = await client.post("/admin/v1/files/upload", files={"file": ("x.txt", b"x")})
        assert denied.status_code == 403
    assert store.data == {}


async def test_same_filename_does_not_overwrite_and_foreign_urls_are_rejected(
    engine, context, authorization
):
    from creativity_service.core.files import FileService

    service = FileService(Store(), authorization)
    first = await service.upload(context, "same.txt", "text/plain", b"first")
    second = await service.upload(context, "same.txt", "text/plain", b"second")
    assert first.url != second.url
    assert await service.read(context, first.url) == b"first"
    foreign = context.model_copy(
        update={"scope": context.scope.model_copy(update={"channel_id": "other"})}
    )
    for owner, url in ((foreign, first.url), (context, "http://127.0.0.1/private")):
        with pytest.raises(ServiceError, match="当前渠道"):
            await service.read(owner, url)
