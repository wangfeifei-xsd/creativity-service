"""样本、导入、任务、对比和人工审阅管理接口。"""

from typing import Annotated

from fastapi import APIRouter, Depends, Request

from creativity_service.core.context import AuthContext, require_http_context
from creativity_service.core.primitives import unavailable
from creativity_service.modules.agents.schemas import AgentValidateInput, AgentValidation
from creativity_service.modules.evaluations.schemas import (
    CaseEdit,
    ComparisonReport,
    DatasetCreate,
    DatasetList,
    DatasetVersionInput,
    DatasetView,
    EvaluationControl,
    EvaluationCreate,
    EvaluationList,
    EvaluationReview,
    EvaluationView,
    ImportInput,
    ImportPreview,
    RerunInput,
    RunCaseInput,
)
from creativity_service.modules.evaluations.services import EvaluationService

router = APIRouter(tags=["效果评测与发布门禁"])
Context = Annotated[AuthContext, Depends(require_http_context)]


def service(request: Request) -> EvaluationService:
    value = getattr(request.app.state, "evaluations", None)
    if not isinstance(value, EvaluationService):
        raise unavailable("效果评测服务")
    return value


Services = Annotated[EvaluationService, Depends(service)]


@router.get("/evaluation-datasets", response_model=DatasetList)
async def datasets(context: Context, service: Services) -> DatasetList:
    return await service.datasets(context)


@router.post("/evaluation-datasets", response_model=DatasetView, status_code=201)
async def create_dataset(context: Context, service: Services, body: DatasetCreate) -> DatasetView:
    return await service.create_dataset(context, body)


@router.get("/evaluation-datasets/{identifier}", response_model=DatasetView)
async def dataset(context: Context, service: Services, identifier: str) -> DatasetView:
    return await service.dataset(context, identifier)


@router.post(
    "/evaluation-datasets/{identifier}/versions", response_model=DatasetView, status_code=201
)
async def version(
    context: Context, service: Services, identifier: str, body: DatasetVersionInput
) -> DatasetView:
    return await service.create_version(context, identifier, body)


@router.post("/evaluation-datasets/{identifier}/imports", response_model=ImportPreview)
async def imports(
    context: Context, service: Services, identifier: str, body: ImportInput
) -> ImportPreview:
    return await service.import_cases(context, identifier, body)


@router.post(
    "/evaluation-datasets/{identifier}/from-run", response_model=DatasetView, status_code=201
)
async def from_run(
    context: Context, service: Services, identifier: str, body: RunCaseInput
) -> DatasetView:
    return await service.from_run(context, identifier, body)


@router.patch("/evaluation-cases/{identifier}", response_model=DatasetView)
async def case(context: Context, service: Services, identifier: str, body: CaseEdit) -> DatasetView:
    return await service.edit_case(context, identifier, body)


@router.get("/evaluations", response_model=EvaluationList)
async def evaluations(context: Context, service: Services) -> EvaluationList:
    return await service.list_evaluations(context)


@router.post("/evaluations", response_model=EvaluationView, status_code=201)
async def create(context: Context, service: Services, body: EvaluationCreate) -> EvaluationView:
    return await service.create(context, body)


@router.get("/evaluations/{identifier}", response_model=EvaluationView)
async def evaluation(context: Context, service: Services, identifier: str) -> EvaluationView:
    return await service.detail(context, identifier)


@router.get("/evaluations/{identifier}/comparison", response_model=ComparisonReport)
async def comparison(context: Context, service: Services, identifier: str) -> ComparisonReport:
    return await service.comparison(context, identifier)


@router.post("/evaluations/{identifier}/control", response_model=EvaluationView)
async def control(
    context: Context, service: Services, identifier: str, body: EvaluationControl
) -> EvaluationView:
    return await service.control(context, identifier, body)


@router.post("/evaluations/{identifier}/review", response_model=EvaluationView)
async def review(
    context: Context, service: Services, identifier: str, body: EvaluationReview
) -> EvaluationView:
    return await service.review(context, identifier, body)


@router.post("/evaluation-results/{identifier}/rerun", response_model=EvaluationView)
async def rerun(
    context: Context, service: Services, identifier: str, body: RerunInput
) -> EvaluationView:
    return await service.rerun(context, identifier, body)


@router.post("/agent-versions/{identifier}/release-check", response_model=AgentValidation)
async def release_check(
    context: Context, service: Services, identifier: str, body: AgentValidateInput
) -> AgentValidation:
    return await service.agents.validate(
        context, identifier, body.model_copy(update={"purpose": "production"})
    )


@router.post("/evaluation-results/{identifier}/review", response_model=EvaluationView)
async def review_result(
    context: Context, service: Services, identifier: str, body: EvaluationReview
) -> EvaluationView:
    return await service.review_result(context, identifier, body)
