"""统一版本前缀，业务模块在对应路由器中登记。"""

from fastapi import APIRouter

from creativity_service.api.artifacts import router as artifact_router
from creativity_service.api.errors import ErrorResponse
from creativity_service.modules.agents.api import router as agents_router
from creativity_service.modules.channels.api import auth_router
from creativity_service.modules.channels.api import router as channels_router
from creativity_service.modules.conversations.api import router as conversations_router
from creativity_service.modules.data_lifecycle.api import router as lifecycle_router
from creativity_service.modules.evaluations.api import router as evaluations_router
from creativity_service.modules.iam.api import router as iam_router
from creativity_service.modules.integrations.api import router as integrations_router
from creativity_service.modules.mcp.api import router as mcp_router
from creativity_service.modules.memory.api import admin_router as memory_admin_router
from creativity_service.modules.memory.api import router as memory_router
from creativity_service.modules.models.api import router as models_router
from creativity_service.modules.prompts.api import router as prompts_router
from creativity_service.modules.runs.api import admin_router as runs_admin_router
from creativity_service.modules.runs.api import router as runs_router
from creativity_service.modules.skills.api import router as skills_router
from creativity_service.modules.tools.api import router as tools_router
from creativity_service.modules.usage.api import router as usage_router

ADMIN_PREFIX = "/admin/v1"
API_PREFIX = "/api/v1"

admin_router = APIRouter(prefix=ADMIN_PREFIX)
api_router = APIRouter(
    prefix=API_PREFIX, responses={status: {"model": ErrorResponse} for status in (410, 502, 504)}
)

admin_router.include_router(artifact_router)
admin_router.include_router(iam_router)
admin_router.include_router(channels_router)
api_router.include_router(artifact_router)
api_router.include_router(auth_router)

admin_router.include_router(prompts_router)
admin_router.include_router(tools_router)

admin_router.include_router(models_router)

admin_router.include_router(usage_router)

admin_router.include_router(mcp_router)

admin_router.include_router(skills_router)

admin_router.include_router(integrations_router)

admin_router.include_router(conversations_router)
api_router.include_router(conversations_router)

admin_router.include_router(memory_router)
admin_router.include_router(memory_admin_router)
api_router.include_router(memory_router)

admin_router.include_router(agents_router)
api_router.include_router(runs_router)
admin_router.include_router(runs_admin_router)

admin_router.include_router(evaluations_router)
admin_router.include_router(lifecycle_router)
api_router.include_router(lifecycle_router)
