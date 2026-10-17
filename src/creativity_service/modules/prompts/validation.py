"""提示词发布只检查模板配置和资源状态，不依赖模型测试记录。"""

from typing import Literal

from creativity_service.core.context import AuthContext
from creativity_service.core.contracts import ResourceVersion
from creativity_service.core.database import UnitOfWork
from creativity_service.core.primitives import ServiceError
from creativity_service.modules.prompts.rendering import validate_content
from creativity_service.modules.prompts.repositories import repository
from creativity_service.modules.prompts.schemas import PromptContent


class PromptVersionValidator:
    async def validate(
        self,
        uow: UnitOfWork,
        context: AuthContext,
        version: ResourceVersion,
        operation: Literal["freeze", "release"],
    ) -> None:
        uow.require_scope(context.scope)
        if version.resource_type != "prompt":
            raise ServiceError("PROMPT_VERSION_INVALID", "发布配置不属于提示词")
        validate_content(PromptContent.model_validate(version.content))
        if version.dependency_version_ids or version.output_schema:
            raise ServiceError(
                "PROMPT_DEPENDENCY_INVALID", "提示词不能携带未声明的配置依赖或智能体输出结构"
            )
        resource = await repository("prompts", context.scope).get(
            uow.connection, version.resource_id
        )
        if resource is None or resource["is_deleted"] or resource["status"] != "ACTIVE":
            raise ServiceError("PROMPT_UNAVAILABLE", "提示词资源不可用")
