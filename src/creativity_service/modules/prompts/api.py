"""提示词管理接口只负责传输；所有业务决定交给服务。"""

from typing import Annotated, cast

from fastapi import APIRouter, Depends, FastAPI, Request, Response
from starlette.responses import JSONResponse

from creativity_service.api.errors import FieldError, error_response
from creativity_service.core.context import AuthContext, require_http_context
from creativity_service.core.contracts import Artifact
from creativity_service.core.primitives import unavailable
from creativity_service.modules.prompts.debug import PromptDebugService
from creativity_service.modules.prompts.rendering import PromptFieldError
from creativity_service.modules.prompts.schemas import (
    PromptCompareView,
    PromptCreate,
    PromptDraftCreate,
    PromptDraftEdit,
    PromptExportRequest,
    PromptImportRequest,
    PromptListView,
    PromptReference,
    PromptReleaseRequest,
    PromptReleaseView,
    PromptRenderRequest,
    PromptRenderView,
    PromptRetireRequest,
    PromptRouteOption,
    PromptSampleCreate,
    PromptSampleView,
    PromptTestRequest,
    PromptTestView,
    PromptUpdate,
    PromptVersionView,
    PromptView,
)
from creativity_service.modules.prompts.services import PromptService

router = APIRouter(tags=["提示词管理"])
Context = Annotated[AuthContext, Depends(require_http_context)]


def services(request: Request, response: Response) -> PromptService:
    response.headers["Cache-Control"] = "no-store"
    value = getattr(request.app.state, "prompts", None)
    if value is None:
        raise unavailable("提示词管理服务")
    return cast(PromptService, value)


Service = Annotated[PromptService, Depends(services)]


@router.get("/prompts")
async def list_prompts(context: Context, service: Service) -> PromptListView:
    return await service.list_items(context)


@router.post("/prompts", status_code=201)
async def create_prompt(body: PromptCreate, context: Context, service: Service) -> PromptView:
    return await service.create(context, body)


@router.post("/prompts/import", status_code=201)
async def import_prompt(
    body: PromptImportRequest, context: Context, service: Service
) -> PromptVersionView:
    return await service.import_content(context, body)


@router.get("/prompt-model-routes")
async def model_routes(context: Context, service: Service) -> list[PromptRouteOption]:
    return await service.route_options(context)


@router.get("/prompts/{prompt_id}")
async def prompt_detail(prompt_id: str, context: Context, service: Service) -> PromptView:
    return await service.detail(context, prompt_id)


@router.patch("/prompts/{prompt_id}")
async def update_prompt(
    prompt_id: str, body: PromptUpdate, context: Context, service: Service
) -> PromptView:
    return await service.update(context, prompt_id, body)


@router.get("/prompts/{prompt_id}/versions")
async def versions(prompt_id: str, context: Context, service: Service) -> list[PromptVersionView]:
    return await service.list_versions(context, prompt_id)


@router.post("/prompts/{prompt_id}/versions", status_code=201)
async def create_draft(
    prompt_id: str, body: PromptDraftCreate, context: Context, service: Service
) -> PromptVersionView:
    return await service.create_draft(context, prompt_id, body)


@router.get("/prompt-versions/{version_id}")
async def version_detail(version_id: str, context: Context, service: Service) -> PromptVersionView:
    return await service.version_detail(context, version_id)


@router.patch("/prompt-versions/{version_id}")
async def edit_draft(
    version_id: str, body: PromptDraftEdit, context: Context, service: Service
) -> PromptVersionView:
    return await service.edit_draft(context, version_id, body)


@router.post("/prompt-versions/{version_id}/render")
async def render_preview(
    version_id: str, body: PromptRenderRequest, context: Context, service: Service
) -> PromptRenderView:
    return await service.preview(context, version_id, body)


@router.get("/prompts/{prompt_id}/samples")
async def samples(
    prompt_id: str, context: Context, service: Service, reveal: bool = False
) -> list[PromptSampleView]:
    return await service.samples(context, prompt_id, reveal)


@router.post("/prompts/{prompt_id}/samples", status_code=201)
async def create_sample(
    prompt_id: str, body: PromptSampleCreate, context: Context, service: Service
) -> PromptSampleView:
    return await service.create_sample(context, prompt_id, body)


@router.post("/prompt-versions/{version_id}/test-descriptors", status_code=201)
async def prepare_test(
    version_id: str, body: PromptTestRequest, context: Context, service: Service
) -> PromptTestView:
    debug = PromptDebugService(service)
    descriptor = await debug.prepare(context, version_id, body)
    return await debug.detail(context, descriptor.test_id)


@router.post("/prompt-versions/{version_id}/tests", status_code=201)
async def start_test(
    version_id: str, body: PromptTestRequest, context: Context, service: Service
) -> PromptTestView:
    return await PromptDebugService(service).start(context, version_id, body)


@router.get("/prompt-versions/{version_id}/tests")
async def tests(version_id: str, context: Context, service: Service) -> list[PromptTestView]:
    return await PromptDebugService(service).list_items(context, version_id)


@router.get("/prompt-tests/{test_id}")
async def test_detail(
    test_id: str, context: Context, service: Service, reveal: bool = False
) -> PromptTestView:
    return await PromptDebugService(service).detail(context, test_id, reveal)


@router.post("/prompt-tests/{test_id}/submit")
async def submit_test(test_id: str, context: Context, service: Service) -> PromptTestView:
    return await PromptDebugService(service).submit(context, test_id)


@router.get("/prompt-versions/{version_id}/compare/{previous_version_id}")
async def compare(
    version_id: str, previous_version_id: str, context: Context, service: Service
) -> PromptCompareView:
    return await service.compare(context, version_id, previous_version_id)


@router.get("/prompts/{prompt_id}/releases")
async def releases(prompt_id: str, context: Context, service: Service) -> list[PromptReleaseView]:
    return await service.releases(context, prompt_id)


@router.post("/prompts/{prompt_id}/releases")
async def release(
    prompt_id: str, body: PromptReleaseRequest, context: Context, service: Service
) -> PromptReleaseView:
    return await service.release(context, prompt_id, body)


@router.post("/prompt-versions/{version_id}/retire")
async def retire(
    version_id: str, body: PromptRetireRequest, context: Context, service: Service
) -> PromptVersionView:
    return await service.retire(context, version_id, body.revision)


@router.get("/prompts/{prompt_id}/references")
async def references(prompt_id: str, context: Context, service: Service) -> list[PromptReference]:
    return await service.references(context, prompt_id)


@router.post("/prompt-versions/{version_id}/exports", status_code=201)
async def export(
    version_id: str, body: PromptExportRequest, context: Context, service: Service
) -> Artifact:
    return await service.export_content(context, version_id, body.format)


def register_prompt_errors(app: "FastAPI") -> None:
    async def variable_error(_request: Request, exc: PromptFieldError) -> JSONResponse:
        return error_response(
            exc.status,
            exc.code,
            exc.message,
            [FieldError.model_validate(field) for field in exc.fields],
            headers={"Cache-Control": "no-store"},
        )

    app.add_exception_handler(PromptFieldError, variable_error)  # type: ignore[arg-type]
