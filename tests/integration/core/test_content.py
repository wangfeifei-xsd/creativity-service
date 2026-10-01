"""内容授权、删除竞争、孤儿回收及恢复核对。"""

from datetime import timedelta

import pytest

from creativity_service.core.artifacts import ArtifactService, S3ObjectStore
from creativity_service.core.config import Settings
from creativity_service.core.database import Repository, transaction
from creativity_service.core.database.tables import metadata
from creativity_service.core.deletion import (
    CleanupRegistry,
    ContentRef,
    DeletionService,
    RecoveryProof,
    RecoveryService,
    content_key,
)
from creativity_service.core.deletion.handlers import register_core_handlers
from creativity_service.core.infrastructure import Infrastructure
from creativity_service.core.locking import record_key
from creativity_service.core.primitives import ServiceError, utcnow

pytestmark = pytest.mark.integration


class MemoryStore:
    def __init__(self):
        self.objects = {}
        self.on_put = None
        self.on_get = None

    async def put(self, key, data, content_type):
        self.objects[key] = data
        if self.on_put:
            await self.on_put()

    async def get(self, key, max_bytes):
        value = self.objects[key]
        if self.on_get:
            await self.on_get()
        return value

    async def delete(self, key):
        self.objects.pop(key, None)


async def test_late_upload_blocked_when_source_deleted(engine, context, authorization):
    store = MemoryStore()
    source = ContentRef("message", "message_one")

    async def delete_source():
        await DeletionService(engine, authorization).mark(context, source, "USER_REQUEST")

    store.on_put = delete_source
    artifacts = ArtifactService(engine, store, authorization)
    with pytest.raises(ServiceError) as error:
        await artifacts.upload(context, "报告.txt", "text/plain", b"private", [source])
    assert error.value.code == "CONTENT_DELETED"
    async with engine.connect() as connection:
        rows = await Repository(metadata.tables["artifacts"], context.scope).find(connection)
    assert len(rows) == 1 and rows[0]["state"] == "STAGED"
    with pytest.raises(ServiceError):
        await artifacts.download(context, rows[0]["id"])


async def test_realtime_authorization_and_transitive_source_deletion(
    engine, context, authorization
):
    store = MemoryStore()
    service = ArtifactService(engine, store, authorization)
    first = await service.upload(
        context, "原文.txt", "text/plain", b"source", [ContentRef("message", "original")]
    )
    second = await service.upload(
        context, "摘要.txt", "text/plain", b"derived", [ContentRef("artifact", first.artifact_id)]
    )
    authorization.denied = True
    with pytest.raises(ServiceError) as error:
        await service.download(context, second.artifact_id)
    assert error.value.status == 403
    authorization.denied = False

    async def delete_during_read():
        await DeletionService(engine, authorization).mark(
            context, ContentRef("message", "original"), "USER_REQUEST"
        )

    store.on_get = delete_during_read
    with pytest.raises(ServiceError) as error:
        await service.download(context, second.artifact_id)
    assert error.value.code == "CONTENT_DELETED"
    registry = CleanupRegistry()
    register_core_handlers(registry, engine, service, authorization)
    await registry.clean(context, ContentRef("artifact", second.artifact_id))
    assert b"derived" not in store.objects.values()


async def test_expired_staging_collected_and_upload_cannot_register(engine, context, authorization):
    store = MemoryStore()

    async def fail():
        raise OSError("模拟对象上传中断")

    store.on_put = fail
    service = ArtifactService(engine, store, authorization)
    with pytest.raises(OSError):
        await service.upload(
            context, "未完成.txt", "text/plain", b"orphan", [ContentRef("message", "source")]
        )
    repo = Repository(metadata.tables["artifacts"], context.scope)
    async with engine.connect() as connection:
        (row,) = await repo.find(connection)
    async with transaction(
        engine,
        context.scope,
        [content_key(context.scope), record_key(context.scope.channel_id, "artifacts", row["id"])],
    ) as uow:
        await repo.change(
            uow, row["id"], row["revision"], {"upload_expires_at": utcnow() - timedelta(seconds=1)}
        )
    assert await service.cleanup_orphans(context) == 1
    assert not store.objects
    assert await service.cleanup_orphans(context) == 1


async def test_restore_barrier_requires_current_independent_proof(engine, context, authorization):
    service = ArtifactService(engine, MemoryStore(), authorization)
    recovery = RecoveryService(engine, authorization)
    recovery_id = await recovery.block(context)
    with pytest.raises(ServiceError, match="恢复核对"):
        await service.upload(
            context, "文件.txt", "text/plain", b"x", [ContentRef("message", "source")]
        )
    with pytest.raises(ServiceError, match="独立删除账本"):
        await recovery.complete(context, recovery_id, None)

    class Verifier:
        async def reconcile(self, scope, identifier):
            async with transaction(engine, scope, [content_key(scope)]) as uow:
                return RecoveryProof(scope, identifier, await recovery.marker_digest(uow, scope))

    await recovery.complete(context, recovery_id, Verifier())
    artifact = await service.upload(
        context, "文件.txt", "text/plain", b"x", [ContentRef("message", "source")]
    )
    assert (await service.download(context, artifact.artifact_id))[0] == b"x"
    await DeletionService(engine, authorization).mark(
        context, ContentRef("message", "source"), "USER_REQUEST"
    )
    newer = await recovery.block(context)
    with pytest.raises(ServiceError, match="恢复证明"):
        await recovery.complete(context, recovery_id, Verifier())
    await recovery.complete(context, newer, Verifier())
    with pytest.raises(ServiceError, match="已删除"):
        await service.download(context, artifact.artifact_id)


async def test_real_s3_private_upload_download_cleanup(engine, context, authorization):
    infrastructure = Infrastructure(Settings())
    service = ArtifactService(
        engine, S3ObjectStore(infrastructure.s3, infrastructure.bucket), authorization
    )
    source = ContentRef("message", "source_real")
    try:
        artifact = await service.upload(
            context, "真实存储.txt", "text/plain", "测试文件".encode(), [source]
        )
        assert (await service.download(context, artifact.artifact_id))[0] == "测试文件".encode()
        await DeletionService(engine, authorization).mark(context, source, "USER_REQUEST")
        await service.clean(context, ContentRef("artifact", artifact.artifact_id))
    finally:
        await infrastructure.close()


async def test_artifacts_isolate_channel_environment_domain_and_subject(
    engine, context, authorization
):
    from creativity_service.core.context import Scope

    original = context.model_copy(
        update={
            "scope": Scope(
                channel_id=context.scope.channel_id,
                environment="test",
                data_scope_id="club_a",
                subject_type="member",
                subject_id="same",
            )
        }
    )
    service = ArtifactService(engine, MemoryStore(), authorization)
    await RecoveryService(engine, authorization).initialize_fresh(original)
    artifact = await service.upload(
        original, "私有.txt", "text/plain", b"private", [ContentRef("message", "same")]
    )
    alternatives = [
        original.scope.model_copy(update={"channel_id": "other_channel"}),
        original.scope.model_copy(update={"environment": "prod"}),
        original.scope.model_copy(update={"data_scope_id": "club_b"}),
        original.scope.model_copy(update={"subject_type": "admin"}),
        original.scope.model_copy(update={"subject_id": "someone_else"}),
    ]
    for scope in alternatives:
        foreign = original.model_copy(update={"scope": scope})
        await RecoveryService(engine, authorization).initialize_fresh(foreign)
        with pytest.raises(ServiceError) as error:
            await service.download(foreign, artifact.artifact_id)
        assert error.value.status == 404
    with pytest.raises(ServiceError) as error:
        await service.download(context, artifact.artifact_id)
    assert error.value.status == 404


async def test_http_download_uses_live_session_and_attachment_headers(
    engine, context, authorization
):
    from httpx import ASGITransport, AsyncClient

    from creativity_service.app import create_schema_app
    from creativity_service.core.services import build_core_services

    services = build_core_services(engine, MemoryStore(), authorization)
    artifact = await services.artifacts.upload(
        context, "报告.csv", "text/csv", b"value\n1", [ContentRef("run", "report_source")]
    )
    verified = context.model_copy(update={"session_id": "session_demo", "token_digest": "a" * 64})

    class Resolver:
        async def authenticate(self, bearer, purpose):
            if bearer != "valid" or authorization.denied:
                raise ServiceError("UNAUTHENTICATED", "请重新登录", 401)
            return verified

    app = create_schema_app()
    app.state.authentication = Resolver()
    app.state.core = services
    async with AsyncClient(transport=ASGITransport(app), base_url="http://test") as client:
        response = await client.get(
            artifact.download_path, headers={"Authorization": "Bearer valid"}
        )
        assert response.status_code == 200 and response.content == b"value\n1"
        assert response.headers["Cache-Control"] == "private, no-store"
        assert response.headers["Content-Disposition"].startswith("attachment;")
        authorization.denied = True
        response = await client.get(
            artifact.download_path, headers={"Authorization": "Bearer valid"}
        )
        assert response.status_code == 401
