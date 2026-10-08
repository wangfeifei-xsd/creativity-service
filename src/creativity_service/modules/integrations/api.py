"""业务接入管理接口；业务执行复用统一运行接口。"""

from typing import Annotated

from fastapi import APIRouter, Depends, Request, Response

from creativity_service.core.context import AuthContext, require_http_context
from creativity_service.core.primitives import unavailable
from creativity_service.modules.integrations.assembly import IntegrationServices
from creativity_service.modules.integrations.schemas import (
    DelegationKeyCreate,
    DelegationKeyIssued,
    DelegationKeyOptions,
    DelegationKeyRotate,
    DelegationKeyView,
    NamedOption,
    RevisionInput,
)
from creativity_service.modules.integrations.subject_contracts import (
    SubjectReviewSave,
    SubjectReviewView,
)

router = APIRouter(tags=["业务接入"])
Context = Annotated[AuthContext, Depends(require_http_context)]


def services(request: Request) -> IntegrationServices:
    bundle: IntegrationServices | None = getattr(request.app.state, "integrations", None)
    if bundle is None:
        raise unavailable("业务接入服务")
    return bundle


Services = Annotated[IntegrationServices, Depends(services)]


@router.get("/subject-review-bindings", response_model=list[SubjectReviewView])
async def subject_reviews(context: Context, service: Services) -> list[SubjectReviewView]:
    if service.subject_review is None:
        raise unavailable("主体复核配置服务")
    return await service.subject_review.list_bindings(context)


@router.post("/subject-review-bindings", response_model=SubjectReviewView)
async def save_subject_review(
    context: Context, service: Services, body: SubjectReviewSave
) -> SubjectReviewView:
    if service.subject_review is None:
        raise unavailable("主体复核配置服务")
    return await service.subject_review.save(context, body)


@router.get("/subject-review-bindings/options", response_model=list[NamedOption])
async def subject_review_options(context: Context, service: Services) -> list[NamedOption]:
    if service.subject_review is None:
        raise unavailable("主体复核配置服务")
    return await service.subject_review.clients(context)


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
