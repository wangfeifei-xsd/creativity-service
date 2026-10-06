"""技能管理接口；不接受运行时工具白名单、渠道或脚本执行参数。"""

from typing import Annotated

from fastapi import APIRouter, Depends, Query, Request

from creativity_service.core.context import AuthContext, require_http_context
from creativity_service.core.contracts import Artifact
from creativity_service.core.primitives import unavailable
from creativity_service.modules.skills.schemas import (
    SkillAgentOption,
    SkillCreate,
    SkillDetail,
    SkillEdit,
    SkillFileContent,
    SkillImport,
    SkillImportPreview,
    SkillImportPreviewInput,
    SkillList,
    SkillTestInput,
    SkillTestView,
    SkillToolOption,
    SkillValidation,
    SkillVersionEdit,
    SkillVersionView,
)
from creativity_service.modules.skills.services import SkillService

router = APIRouter(tags=["技能管理"])
Context = Annotated[AuthContext, Depends(require_http_context)]


def service(request: Request) -> SkillService:
    value: SkillService | None = getattr(request.app.state, "skills", None)
    if value is None:
        raise unavailable("技能管理服务")
    return value


Services = Annotated[SkillService, Depends(service)]


@router.get("/skills", response_model=SkillList)
async def skills(
    context: Context, service: Services, search: Annotated[str | None, Query(max_length=128)] = None
) -> SkillList:
    return await service.list_skills(context, search)


@router.post("/skills", response_model=SkillDetail, status_code=201)
async def create(context: Context, service: Services, body: SkillCreate) -> SkillDetail:
    return await service.create(context, body)


@router.post("/skills/imports", response_model=SkillDetail, status_code=201)
async def imports(context: Context, service: Services, body: SkillImport) -> SkillDetail:
    return await service.import_package(context, body)


@router.post("/skills/imports/preview", response_model=SkillImportPreview)
async def preview_import(
    context: Context, service: Services, body: SkillImportPreviewInput
) -> SkillImportPreview:
    return await service.preview_import(context, body)


@router.get("/skills/agent-options", response_model=list[SkillAgentOption])
async def agent_options(context: Context, service: Services) -> list[SkillAgentOption]:
    return await service.agent_options(context)


@router.get("/skills/tool-options", response_model=list[SkillToolOption])
async def tool_options(context: Context, service: Services) -> list[SkillToolOption]:
    return await service.tool_options(context)


@router.get("/skills/{skill_id}", response_model=SkillDetail)
async def detail(context: Context, service: Services, skill_id: str) -> SkillDetail:
    return await service.detail(context, skill_id)


@router.patch("/skills/{skill_id}", response_model=SkillDetail)
async def edit(context: Context, service: Services, skill_id: str, body: SkillEdit) -> SkillDetail:
    return await service.edit(context, skill_id, body)


@router.get("/skill-versions/{version_id}", response_model=SkillVersionView)
async def version(context: Context, service: Services, version_id: str) -> SkillVersionView:
    return await service.version(context, version_id)


@router.patch("/skill-versions/{version_id}", response_model=SkillVersionView)
async def edit_version(
    context: Context, service: Services, version_id: str, body: SkillVersionEdit
) -> SkillVersionView:
    return await service.edit_version(context, version_id, body)


@router.get("/skill-versions/{version_id}/files", response_model=SkillFileContent)
async def file(
    context: Context,
    service: Services,
    version_id: str,
    path: Annotated[str, Query(max_length=1024)],
) -> SkillFileContent:
    return await service.file(context, version_id, path)


@router.post("/skill-versions/{version_id}/validate", response_model=SkillValidation)
async def validate(context: Context, service: Services, version_id: str) -> SkillValidation:
    return await service.validate(context, version_id)


@router.post("/skill-versions/{version_id}/tests", response_model=SkillTestView)
async def test(
    context: Context, service: Services, version_id: str, body: SkillTestInput
) -> SkillTestView:
    return await service.test(context, version_id, body)


@router.get("/skill-versions/{version_id}/tests", response_model=list[SkillTestView])
async def tests(context: Context, service: Services, version_id: str) -> list[SkillTestView]:
    return await service.tests(context, version_id)


@router.post("/skill-versions/{version_id}/exports", response_model=Artifact)
async def export(context: Context, service: Services, version_id: str) -> Artifact:
    return await service.export(context, version_id)


@router.get("/skills/{skill_id}/configuration")
async def configuration(context: Context, service: Services, skill_id: str) -> SkillVersionView:
    return await service.version(context, skill_id)


@router.patch("/skills/{skill_id}/configuration")
async def edit_configuration(
    context: Context, service: Services, skill_id: str, body: SkillVersionEdit
) -> SkillVersionView:
    return await service.edit_version(context, skill_id, body)
