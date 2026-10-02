"""统一版本前缀，业务模块在对应路由器中登记。"""

from fastapi import APIRouter

from creativity_service.api.artifacts import router as artifact_router
from creativity_service.modules.channels.api import auth_router
from creativity_service.modules.channels.api import router as channels_router
from creativity_service.modules.iam.api import router as iam_router
from creativity_service.modules.models.api import router as models_router
from creativity_service.modules.prompts.api import router as prompts_router
from creativity_service.modules.tools.api import router as tools_router
from creativity_service.modules.usage.api import router as usage_router

ADMIN_PREFIX = "/admin/v1"
API_PREFIX = "/api/v1"

admin_router = APIRouter(prefix=ADMIN_PREFIX)
api_router = APIRouter(prefix=API_PREFIX)

admin_router.include_router(artifact_router)
admin_router.include_router(iam_router)
admin_router.include_router(channels_router)
api_router.include_router(artifact_router)
api_router.include_router(auth_router)

admin_router.include_router(prompts_router)
admin_router.include_router(tools_router)

admin_router.include_router(models_router)

admin_router.include_router(usage_router)
