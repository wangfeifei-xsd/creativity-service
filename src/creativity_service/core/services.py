"""公共设施装配；认证、授权、发布门禁与密钥来源由所属模块注入。"""

from collections.abc import Callable
from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncEngine

from creativity_service.core.artifacts import ArtifactService, ObjectStore
from creativity_service.core.context import Authorization, DenyAuthorization
from creativity_service.core.database.tables import metadata
from creativity_service.core.deletion import CleanupRegistry, DeletionService, RecoveryService
from creativity_service.core.deletion.handlers import register_core_handlers
from creativity_service.core.security.credentials import CredentialService, KeyProvider
from creativity_service.core.versioning import VersionService, VersionValidator


@dataclass(frozen=True)
class CoreServices:
    artifacts: ArtifactService
    versions: VersionService
    credentials: CredentialService
    deletion: DeletionService
    recovery: RecoveryService
    cleanup: CleanupRegistry


def build_core_services(
    engine: AsyncEngine,
    store: ObjectStore,
    authorization: Authorization | None = None,
    version_validator: VersionValidator | None = None,
    key_provider: KeyProvider | None = None,
    configure_cleanup: Callable[[CleanupRegistry], None] | None = None,
) -> CoreServices:
    authorization = authorization or DenyAuthorization()
    artifacts = ArtifactService(engine, store, authorization)
    registry = CleanupRegistry()
    register_core_handlers(registry, engine, artifacts, authorization)
    if configure_cleanup is not None:
        configure_cleanup(registry)
    required = {
        table.info["cleanup_type"]
        for table in metadata.tables.values()
        if table.info.get("content")
    }
    registry.validate(required)
    return CoreServices(
        artifacts,
        VersionService(engine, authorization, version_validator),
        CredentialService(engine, key_provider, authorization),
        DeletionService(engine, authorization),
        RecoveryService(engine, authorization),
        registry,
    )
