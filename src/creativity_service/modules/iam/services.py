"""IAM 装配入口，05 和 18 只需注入其所属当前状态读取器。"""

from dataclasses import dataclass

from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncEngine

from creativity_service.core.auth.authentication import AuthenticationService
from creativity_service.core.auth.passwords import PasswordHasher
from creativity_service.core.auth.tokens import TokenStore
from creativity_service.core.auth.types import (
    ResourceStateReader,
    ServiceIdentityReader,
    SubjectAuthorityReader,
    WorkspaceDirectory,
)
from creativity_service.core.context import ChannelStateReader
from creativity_service.modules.iam.access import AccessService
from creativity_service.modules.iam.accounts import AccountService
from creativity_service.modules.iam.audit import AuditService
from creativity_service.modules.iam.authorization import IamAuthorization
from creativity_service.modules.iam.captcha import CaptchaService
from creativity_service.modules.iam.repositories import IdentityRepository
from creativity_service.modules.iam.revocations import RevocationService
from creativity_service.modules.iam.sessions import SessionService


@dataclass(frozen=True)
class IamServices:
    authentication: AuthenticationService
    authorization: IamAuthorization
    accounts: AccountService
    access: AccessService
    sessions: SessionService
    audit: AuditService
    revocations: RevocationService
    captcha: CaptchaService


def build_iam_services(
    engine: AsyncEngine,
    redis: Redis,
    prefix: str,
    *,
    management_ttl: int = 7200,
    service_ttl: int = 3600,
    channels: ChannelStateReader | None = None,
    services: ServiceIdentityReader | None = None,
    resources: ResourceStateReader | None = None,
    subjects: SubjectAuthorityReader | None = None,
    directory: WorkspaceDirectory | None = None,
) -> IamServices:
    repository = IdentityRepository(engine)
    tokens = TokenStore(redis, prefix, management_ttl, service_ttl)
    passwords = PasswordHasher()
    authentication = AuthenticationService(tokens, repository, channels, services)
    authorization = IamAuthorization(authentication, resources, subjects)
    revocations = RevocationService(repository, tokens)
    captcha = CaptchaService(redis, prefix)
    return IamServices(
        authentication,
        authorization,
        AccountService(repository, authentication, passwords, revocations),
        AccessService(repository, authentication, authorization, revocations, directory),
        SessionService(
            repository, authentication, authorization, passwords, revocations, captcha, directory
        ),
        AuditService(repository, authorization),
        revocations,
        captcha,
    )
