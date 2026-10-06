"""模型管理传输层；连接检查只读供应商目录，能力调用进入统一运行时。"""

from typing import Annotated, cast

from fastapi import APIRouter, Depends, Request

from creativity_service.core.contracts import ResourceVersion
from creativity_service.core.primitives import unavailable
from creativity_service.modules.iam.api import Session
from creativity_service.modules.iam.schemas import GrantView
from creativity_service.modules.models.assembly import ModelServices
from creativity_service.modules.models.policy import PROTOCOLS
from creativity_service.modules.models.schemas import (
    CaseDefinition,
    ConnectionInput,
    ConnectionList,
    ConnectionTestView,
    ConnectionView,
    CredentialInput,
    CredentialView,
    ModelGrantInput,
    ModelInput,
    ModelList,
    ModelView,
    PriceView,
    ProtocolView,
    ProviderInput,
    ProviderView,
    ReleaseInput,
    RouteInput,
    RouteList,
    RouteVersionInput,
    RouteView,
    TestInput,
    TestView,
)
from creativity_service.modules.models.testing import CASES

router = APIRouter(tags=["模型配置"])


def services(request: Request) -> ModelServices:
    value = getattr(request.app.state, "models", None)
    if value is None:
        raise unavailable("模型配置服务")
    return cast(ModelServices, value)


Services = Annotated[ModelServices, Depends(services)]


@router.get("/model-protocols", response_model=list[ProtocolView])
async def protocols(session: Session, service: Services) -> list[ProtocolView]:
    await service.configuration.providers(session)
    return list(PROTOCOLS.values())


@router.get("/model-providers", response_model=list[ProviderView])
async def providers(session: Session, service: Services) -> list[ProviderView]:
    return await service.configuration.providers(session)


@router.post("/model-providers", response_model=ProviderView, status_code=201)
async def save_provider(body: ProviderInput, session: Session, service: Services) -> ProviderView:
    return await service.configuration.save_provider(session, body)


@router.post("/model-credentials", response_model=CredentialView, status_code=201)
async def credential(body: CredentialInput, session: Session, service: Services) -> CredentialView:
    return CredentialView(
        credential_ref=await service.configuration.store_credential(session, body)
    )


@router.get("/model-connections", response_model=ConnectionList)
async def connections(session: Session, service: Services) -> ConnectionList:
    return await service.configuration.connections(session)


@router.post("/model-connections", response_model=ConnectionView, status_code=201)
async def create_connection(
    body: ConnectionInput, session: Session, service: Services
) -> ConnectionView:
    return await service.configuration.save_connection(session, body)


@router.patch("/model-connections/{connection_id}", response_model=ConnectionView)
async def update_connection(
    connection_id: str, body: ConnectionInput, session: Session, service: Services
) -> ConnectionView:
    return await service.configuration.save_connection(session, body, connection_id)


@router.get("/model-connections/{connection_id}/versions", response_model=list[ResourceVersion])
async def connection_history(
    connection_id: str, session: Session, service: Services
) -> list[ResourceVersion]:
    return await service.configuration.history(session, "model_connection", connection_id)


@router.get("/models", response_model=ModelList)
async def models(session: Session, service: Services) -> ModelList:
    return await service.configuration.models(session)


@router.post("/models", response_model=ModelView, status_code=201)
async def create_model(body: ModelInput, session: Session, service: Services) -> ModelView:
    return await service.configuration.save_model(session, body)


@router.get("/models/{model_id}", response_model=ModelView)
async def model(model_id: str, session: Session, service: Services) -> ModelView:
    return await service.configuration.detail(session, model_id)


@router.patch("/models/{model_id}", response_model=ModelView)
async def update_model(
    model_id: str, body: ModelInput, session: Session, service: Services
) -> ModelView:
    return await service.configuration.save_model(session, body, model_id)


@router.get("/models/{model_id}/versions", response_model=list[ResourceVersion])
async def history(model_id: str, session: Session, service: Services) -> list[ResourceVersion]:
    return await service.configuration.history(session, "model", model_id)


@router.get("/models/{model_id}/prices", response_model=PriceView)
async def prices(model_id: str, session: Session, service: Services) -> PriceView:
    return await service.configuration.price(session, model_id)


@router.get("/model-test-cases", response_model=list[CaseDefinition])
async def cases(session: Session, service: Services) -> list[CaseDefinition]:
    await service.configuration.context(session)
    return list(CASES.values())


@router.post("/models/{model_id}/tests", response_model=TestView, status_code=201)
async def create_test(
    model_id: str, body: TestInput, session: Session, service: Services
) -> TestView:
    return await service.testing.create(session, model_id, body)


@router.post("/models/{model_id}/connection-test", response_model=ConnectionTestView)
async def test_connection(model_id: str, session: Session, service: Services) -> ConnectionTestView:
    return await service.connection_testing.check(session, model_id)


@router.get("/models/{model_id}/tests", response_model=list[TestView])
async def tests(model_id: str, session: Session, service: Services) -> list[TestView]:
    return await service.testing.list(session, model_id)


@router.get("/model-tests/{test_id}", response_model=TestView)
async def test(test_id: str, session: Session, service: Services) -> TestView:
    return await service.testing.get(session, test_id)


@router.get("/model-routes", response_model=RouteList)
async def routes(session: Session, service: Services) -> RouteList:
    return await service.routing.list_items(session)


@router.post("/model-routes", response_model=RouteView, status_code=201)
async def create_route(body: RouteInput, session: Session, service: Services) -> RouteView:
    return await service.routing.create(session, body)


@router.get("/model-routes/{route_id}/versions", response_model=list[ResourceVersion])
async def versions(route_id: str, session: Session, service: Services) -> list[ResourceVersion]:
    return await service.configuration.history(session, "model_route", route_id)


@router.post("/model-routes/{route_id}/versions", response_model=ResourceVersion, status_code=201)
async def create_version(
    route_id: str, body: RouteVersionInput, session: Session, service: Services
) -> ResourceVersion:
    return await service.routing.create_version(session, route_id, body)


@router.post("/model-routes/{route_id}/releases", response_model=ResourceVersion)
async def release(
    route_id: str, body: ReleaseInput, session: Session, service: Services
) -> ResourceVersion:
    return await service.routing.release(session, route_id, body)


@router.put("/channels/{channel_id}/model-grants/{model_id}", response_model=GrantView)
async def grant(
    channel_id: str, model_id: str, body: ModelGrantInput, session: Session, service: Services
) -> GrantView:
    return await service.configuration.grant(session, channel_id, model_id, body)


@router.get("/models/{model_id}/grants", response_model=list[GrantView])
async def grants(model_id: str, session: Session, service: Services) -> list[GrantView]:
    return await service.configuration.grants(session, model_id)
