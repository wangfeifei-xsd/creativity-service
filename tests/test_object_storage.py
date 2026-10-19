"""通过官方 SDK 的真实请求编码验证上传权限、稳定 URL、限长读取与对象范围。"""

import io
from pathlib import Path
from unittest.mock import Mock
from urllib.parse import unquote, urlsplit

import alibabacloud_oss_v2 as oss
import pytest
import requests
from pydantic import ValidationError

from creativity_service.core.artifacts import OSSObjectStore
from creativity_service.core.config import Settings
from creativity_service.core.primitives import ServiceError
from creativity_service.development import dependencies
from scripts import restore_init_objects
from scripts.render_archive import object_bytes


@pytest.fixture
def sdk_transport(monkeypatch):
    requests_seen, pending = [], []

    def send(_session, request, **kwargs):
        requests_seen.append((request, kwargs))
        status, content, headers = pending.pop(0)
        response = requests.Response()
        response.status_code = status
        response.headers["Date"] = "Fri, 16 Oct 2026 00:00:00 GMT"
        response.headers.update(headers)
        response.raw = io.BytesIO(content)
        response.url = request.url
        return response

    monkeypatch.setattr(requests.Session, "send", send)
    return requests_seen, pending


@pytest.mark.parametrize(
    "endpoint,normalized",
    [
        ("oss-cn-beijing.aliyuncs.com/", "https://oss-cn-beijing.aliyuncs.com"),
        (
            "https://test-bucket.oss-cn-beijing.aliyuncs.com/",
            "https://oss-cn-beijing.aliyuncs.com",
        ),
        (
            "https://test-bucket.oss-cn-beijing-internal.aliyuncs.com",
            "https://oss-cn-beijing-internal.aliyuncs.com",
        ),
    ],
)
def test_oss_endpoint_normalizes_bucket_domain(settings, endpoint, normalized):
    configured = Settings(_env_file=None, **(settings.model_dump() | {"oss_endpoint": endpoint}))
    assert configured.oss_endpoint == normalized


@pytest.mark.parametrize(
    "endpoint",
    [
        "http://127.0.0.1:59000",
        "https://oss-cn-heyuan.aliyuncs.com",
        "https://other-bucket.oss-cn-beijing.aliyuncs.com",
        "http://oss-cn-beijing.aliyuncs.com",
        "https://user:secret@oss-cn-beijing.aliyuncs.com",
        "https://oss-cn-beijing.aliyuncs.com/path",
        "https://oss-cn-beijing.aliyuncs.com?query=1",
        "https://oss-cn-beijing.aliyuncs.com#fragment",
    ],
)
def test_storage_rejects_local_or_mismatched_endpoints(settings, endpoint):
    with pytest.raises(ValidationError, match="阿里云 HTTPS 端点"):
        Settings(_env_file=None, **(settings.model_dump() | {"oss_endpoint": endpoint}))


async def test_private_upload_uses_v4_and_deployment_namespace(settings, sdk_transport):
    seen, pending = sdk_transport
    store = OSSObjectStore(settings)
    other = OSSObjectStore(settings.model_copy(update={"environment": "prod"}))
    key = "channels/channel_a/test/中文 空格#.txt"
    pending.append((200, b"", {}))
    try:
        await store.put(key, b"private content", "text/plain", forbid_overwrite=True)
        request, _ = seen[0]
        assert request.method == "PUT"
        assert urlsplit(request.url).netloc == "test-bucket.oss-cn-beijing.aliyuncs.com"
        assert unquote(urlsplit(request.url).path) == "/creativity/test/" + key
        assert request.headers["Authorization"].startswith("OSS4-HMAC-SHA256 ")
        assert "/cn-beijing/oss/aliyun_v4_request" in request.headers["Authorization"]
        assert request.headers["x-oss-object-acl"] == "private"
        assert request.headers["x-oss-forbid-overwrite"] == "true"
        assert request.headers["Content-Type"] == "text/plain"
        assert store.object_key(key) != other.object_key(key)
    finally:
        store.close()
        other.close()


@pytest.mark.parametrize("internal", [False, True])
async def test_public_upload_returns_external_url_even_with_internal_endpoint(
    settings, sdk_transport, internal
):
    seen, pending = sdk_transport
    configured = settings.model_copy(
        update={
            "oss_endpoint": "https://oss-cn-beijing-internal.aliyuncs.com"
            if internal
            else settings.oss_endpoint
        }
    )
    store = OSSObjectStore(configured)
    key = "channels/channel_a/files/file_test/中文 空格#.txt"
    pending.append((200, b"", {}))
    try:
        url = await store.put_public(key, b"public content", "text/plain")
        request, _ = seen[0]
        assert request.headers["x-oss-object-acl"] == "public-read"
        assert request.headers["x-oss-forbid-overwrite"] == "true"
        assert ("-internal" in urlsplit(request.url).netloc) is internal
        assert url.startswith("https://test-bucket.oss-cn-beijing.aliyuncs.com/")
        assert "%23" in url and "%20" in url and "?" not in url
        assert store.key_from_url(url) == key
    finally:
        store.close()


@pytest.mark.parametrize(
    "url",
    [
        "http://127.0.0.1/private",
        "https://test-bucket.oss-cn-beijing.aliyuncs.com/other/channels/a/files/file/x",
        "https://test-bucket.oss-cn-beijing.aliyuncs.com/creativity/prod/channels/a/files/file/x",
        "https://test-bucket.oss-cn-beijing.aliyuncs.com/creativity/test/channels/a/../b",
        "https://test-bucket.oss-cn-beijing.aliyuncs.com/creativity/test/channels/a/%66iles/file/x",
        "https://test-bucket.oss-cn-beijing.aliyuncs.com/creativity/test/channels/a/files/file/x?token=1",
        "https://test-bucket.oss-cn-beijing.aliyuncs.com/creativity/test/channels/a/files/file/x#part",
    ],
)
def test_file_urls_reject_foreign_origins_namespaces_and_ambiguous_paths(settings, url):
    store = OSSObjectStore(settings)
    try:
        with pytest.raises(ValueError):
            store.key_from_url(url)
    finally:
        store.close()


@pytest.mark.parametrize("declared_length", [None, "1", "100"])
async def test_download_checks_actual_size_and_closes_response(settings, declared_length):
    store = OSSObjectStore(settings)
    body = Mock()
    body.iter_bytes.return_value = iter([b"ab", b"cde"])
    store.client = Mock()
    store.client.get_object.return_value = Mock(
        body=body, content_length=int(declared_length) if declared_length else None
    )
    try:
        with pytest.raises(ServiceError, match="文件大小超限"):
            await store.get("channels/channel_a/file", 4)
        body.close.assert_called_once()
        if declared_length == "100":
            body.iter_bytes.assert_not_called()
    finally:
        store.close()


async def test_sdk_error_is_unwrapped_and_missing_object_is_preserved(settings, sdk_transport):
    _, pending = sdk_transport
    store = OSSObjectStore(settings)
    pending.append((404, b"<Error><Code>NoSuchKey</Code><Message>missing</Message></Error>", {}))
    try:
        with pytest.raises(oss.exceptions.ServiceError) as error:
            await store.get("channels/channel_a/missing", 10)
        assert error.value.code == "NoSuchKey"
        assert error.value.status_code == 404
    finally:
        store.close()


def test_readiness_is_scoped_read_only_and_uses_configured_timeout(settings, sdk_transport):
    seen, pending = sdk_transport
    store = OSSObjectStore(settings)
    pending.append((200, b"<ListBucketResult/>", {}))
    try:
        store.check()
        request, options = seen[0]
        assert request.method == "GET"
        assert "prefix=creativity%2Ftest%2Fchannels%2F" in request.url
        assert options["timeout"] == (settings.health_timeout_seconds,) * 2
    finally:
        store.close()


@pytest.mark.parametrize(
    "key", ["other/file", "channels//file", "channels/a/../b", "/channels/a/file"]
)
async def test_invalid_keys_never_reach_oss(settings, key):
    store = OSSObjectStore(settings)
    store.client = Mock()
    try:
        with pytest.raises(ValueError, match="渠道"):
            await store.put(key, b"private", "text/plain")
        with pytest.raises(ValueError, match="渠道"):
            await store.get(key, 100)
        store.client.put_object.assert_not_called()
        store.client.get_object.assert_not_called()
    finally:
        store.close()


def test_oss_startup_failure_is_safe_and_never_starts_local_storage(settings, monkeypatch):
    store = Mock()
    store.check.side_effect = RuntimeError("私密凭据")
    monkeypatch.setattr(dependencies, "OSSObjectStore", Mock(return_value=store))
    docker = Mock(side_effect=AssertionError("不能回退本地存储"))
    monkeypatch.setattr(dependencies, "DockerRuntime", docker)
    with pytest.raises(dependencies.StartupError, match="阿里云 OSS") as error:
        dependencies.prepare_storage(settings)
    assert "私密凭据" not in str(error.value)
    store.close.assert_called_once()
    docker.assert_not_called()


@pytest.mark.parametrize("concurrent", [False, True])
@pytest.mark.parametrize("object_index", [0, 1])
async def test_seed_restore_never_overwrites_and_verifies_concurrent_upload(
    settings, sdk_transport, concurrent, object_index
):
    seen, pending = sdk_transport
    item = restore_init_objects.archived_objects(Path("sql/init_data.sql"))[object_index]
    data = object_bytes(item)
    pending.append((404, b"<Error><Code>NoSuchKey</Code></Error>", {}))
    pending.append(
        (409, b"<Error><Code>FileAlreadyExists</Code></Error>", {})
        if concurrent
        else (200, b"", {})
    )
    pending.append((200, data, {"Content-Length": str(len(data))}))
    await restore_init_objects.restore_objects(settings, [item], check_only=False)
    assert [request.method for request, _ in seen] == ["GET", "PUT", "GET"]
    assert seen[1][0].headers["x-oss-forbid-overwrite"] == "true"
    assert seen[1][0].headers["x-oss-object-acl"] == ("public-read" if object_index else "private")


async def test_seed_check_reports_missing_object_without_uploading(settings, sdk_transport):
    seen, pending = sdk_transport
    item = restore_init_objects.archived_objects(Path("sql/init_data.sql"))[0]
    pending.append((404, b"<Error><Code>NoSuchKey</Code></Error>", {}))
    with pytest.raises(ValueError, match="尚未恢复"):
        await restore_init_objects.restore_objects(settings, [item], check_only=True)
    assert [request.method for request, _ in seen] == ["GET"]
