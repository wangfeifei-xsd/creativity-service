"""将业务单项能力接到 10 的统一工具执行层；运行编号由执行器注入。"""

from creativity_service.core.context import AuthContext
from creativity_service.core.primitives import ServiceError
from creativity_service.integrations.business.base import Operation
from creativity_service.integrations.tools import (
    AdapterRegistration,
    AdapterRegistry,
    AdapterRequest,
    AdapterResult,
)
from creativity_service.modules.integrations.services import IntegrationService


class BusinessToolAdapter:
    def __init__(
        self,
        service: IntegrationService,
        integration_id: str,
        operation: Operation,
        config_revision: int,
    ) -> None:
        self.service, self.integration_id = service, integration_id
        self.operation, self.config_revision = operation, config_revision

    async def invoke(self, request: AdapterRequest) -> AdapterResult:
        if not request.run_id:
            raise ServiceError("RUN_REQUIRED", "业务工具调用缺少受信运行编号", 503)
        connection = await self.service.row(request.context, self.integration_id)
        if connection["revision"] != self.config_revision:
            raise ServiceError(
                "BUSINESS_CONTRACT_CHANGED", "业务连接配置已变化，请更新工具绑定", 409
            )
        result = await self.service.invoke(
            request.context, self.integration_id, self.operation, request.arguments, request.run_id
        )
        return AdapterResult(
            data=result.model_dump(mode="json"),
            source_request_id=result.source_request_id,
            source_version=result.source_version,
            observed_at=result.observed_at,
            cursor=result.cursor,
            has_more=result.has_more,
            coverage={"range": result.coverage},
        )


async def register_business_tools(
    registry: AdapterRegistry,
    service: IntegrationService,
    context: AuthContext,
    integration_id: str,
) -> None:
    """19/20 或受信装配器按已保存的连接登记；模型不能动态选择实现或连接。"""
    await service.require(context)
    row = await service.row(context, integration_id)
    registered = service.registry.resolve(row["adapter_code"], row["adapter_version"])
    for capability in registered.capabilities:
        if capability.operation not in row["allowed_operations"]:
            continue
        registry.register(
            AdapterRegistration(
                key=f"business_{integration_id}_{capability.operation}_{row['revision']}",
                name=f"{row['name']} · {capability.name}",
                source_type="http",
                implementation_version=f"{row['adapter_version']}.{row['revision']}",
                actual_effect="READ_ONLY",
                adapter=BusinessToolAdapter(
                    service, integration_id, capability.operation, row["revision"]
                ),
                channel_id=context.scope.channel_id,
                environment=context.scope.environment,
                connection_id=integration_id,
                sensitive=not capability.public,
            )
        )
