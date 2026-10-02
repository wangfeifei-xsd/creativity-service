"""业务接入管理接口；业务执行复用统一运行接口。"""

from typing import Annotated

from fastapi import APIRouter, Depends, Request, Response
from pydantic import Field, SecretBytes, SecretStr

from creativity_service.core.context import AuthContext, require_http_context
from creativity_service.core.primitives import Contract, unavailable
from creativity_service.modules.integrations.assembly import IntegrationServices
from creativity_service.modules.integrations.schemas import (
    BusinessCapabilityView,
    ContractTestInput,
    ContractTestView,
    DelegationKeyCreate,
    DelegationKeyIssued,
    DelegationKeyOptions,
    DelegationKeyRotate,
    DelegationKeyView,
    IntegrationCreate,
    IntegrationEdit,
    IntegrationList,
    IntegrationOptions,
    IntegrationView,
    RevisionInput,
)

router = APIRouter(tags=["业务接入"])
Context = Annotated[AuthContext, Depends(require_http_context)]


def services(request: Request) -> IntegrationServices:
    bundle: IntegrationServices | None = getattr(request.app.state, "integrations", None)
    if bundle is None:
        raise unavailable("业务接入服务")
    return bundle


Services = Annotated[IntegrationServices, Depends(services)]


class CredentialInput(Contract):
    secret: SecretStr = Field(min_length=1, max_length=4096)


class CredentialReference(Contract):
    credential_ref: str


@router.post(
    "/integration-credentials", response_model=CredentialReference, status_code=201, deprecated=True
)
async def credential(
    context: Context, service: Services, body: CredentialInput
) -> CredentialReference:
    value = body.secret.get_secret_value()
    return CredentialReference(
        credential_ref=await service.credentials.store(
            context, "http_tool", SecretBytes(value.encode())
        )
    )


@router.get("/integrations", response_model=IntegrationList, deprecated=True)
async def integrations(context: Context, service: Services) -> IntegrationList:
    return await service.management.list_integrations(context)


@router.get("/integrations/options", response_model=IntegrationOptions, deprecated=True)
async def options(context: Context, service: Services) -> IntegrationOptions:
    return await service.management.options(context)


@router.post("/integrations", response_model=IntegrationView, status_code=201, deprecated=True)
async def create(context: Context, service: Services, body: IntegrationCreate) -> IntegrationView:
    return await service.management.save(context, body)


@router.get("/integrations/{integration_id}", response_model=IntegrationView, deprecated=True)
async def detail(context: Context, service: Services, integration_id: str) -> IntegrationView:
    return await service.management.detail(context, integration_id)


@router.patch("/integrations/{integration_id}", response_model=IntegrationView, deprecated=True)
async def edit(
    context: Context, service: Services, integration_id: str, body: IntegrationEdit
) -> IntegrationView:
    return await service.management.save(context, body, integration_id)


@router.get(
    "/integrations/{integration_id}/capabilities",
    response_model=list[BusinessCapabilityView],
    deprecated=True,
)
async def capabilities(
    context: Context, service: Services, integration_id: str
) -> list[BusinessCapabilityView]:
    return await service.management.capabilities(context, integration_id)


@router.get(
    "/integrations/{integration_id}/tests", response_model=list[ContractTestView], deprecated=True
)
async def tests(context: Context, service: Services, integration_id: str) -> list[ContractTestView]:
    return await service.management.tests(context, integration_id)


@router.post(
    "/integrations/{integration_id}/tests",
    response_model=ContractTestView,
    status_code=201,
    deprecated=True,
)
async def test(
    context: Context, service: Services, integration_id: str, body: ContractTestInput
) -> ContractTestView:
    return await service.management.test(context, integration_id, body)


@router.get("/delegation-keys", response_model=list[DelegationKeyView])
async def keys(context: Context, service: Services) -> list[DelegationKeyView]:
    return await service.keys.list(context)


@router.post("/delegation-keys", response_model=DelegationKeyIssued, status_code=201)
async def create_key(
    context: Context, service: Services, body: DelegationKeyCreate, response: Response
) -> DelegationKeyIssued:
    response.headers["Cache-Control"] = "no-store"
    return await service.keys.create(context, body)


@router.post("/delegation-keys/{kid}/rotate", response_model=DelegationKeyIssued)
async def rotate(
    context: Context, service: Services, kid: str, body: DelegationKeyRotate, response: Response
) -> DelegationKeyIssued:
    response.headers["Cache-Control"] = "no-store"
    return await service.keys.rotate(context, kid, body)


@router.post("/delegation-keys/{kid}/revoke", response_model=DelegationKeyView)
async def revoke(
    context: Context, service: Services, kid: str, body: RevisionInput
) -> DelegationKeyView:
    return await service.keys.revoke(context, kid, body.revision)


@router.get("/delegation-keys/options", response_model=DelegationKeyOptions)
async def key_options(context: Context, service: Services) -> DelegationKeyOptions:
    return await service.keys.options(context)
