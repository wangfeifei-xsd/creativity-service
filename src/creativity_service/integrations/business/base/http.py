"""标准业务专用接口传输：固定能力路径、授权凭据与有界响应。"""

import asyncio
import json
from collections.abc import Awaitable, Callable

import httpcore
from pydantic import ValidationError

from creativity_service.core.database import assert_external_io_allowed
from creativity_service.core.primitives import canonical_json
from creativity_service.core.security.outbound import OutboundPolicy, ValidatedTarget
from creativity_service.integrations.business.base import (
    BusinessAdapter,
    BusinessCall,
    BusinessResult,
    business_error,
)
from creativity_service.integrations.business.base.validation import map_fields, source_status
from creativity_service.integrations.tools.http import PinnedBackend


class StandardHttpAdapter(BusinessAdapter):
    def __init__(
        self,
        policy: OutboundPolicy,
        credentials: Callable[[BusinessCall], Awaitable[str]],
    ) -> None:
        self.policy, self.credentials = policy, credentials

    async def invoke(self, call: BusinessCall) -> BusinessResult:
        assert_external_io_allowed()
        path = call.connection["operation_paths"].get(call.operation)
        if not path:
            raise business_error("BUSINESS_UNSUPPORTED")
        url = call.connection["business_endpoint"].rstrip("/") + path
        # 目的地白名单来自部署配置；业务参数不能改变地址、方法或请求头。
        target = await self.policy.validate(call.context.scope, "http_tool", url)
        authorization = await self.credentials(call)
        headers = {
            "Authorization": authorization,
            "Accept": "application/json",
            "Content-Type": "application/json",
            "Accept-Encoding": "identity",
            "X-Request-ID": call.context.request_id,
            "X-Run-ID": call.run_id,
        }
        body = canonical_json(
            {
                "operation": call.operation,
                "arguments": call.arguments,
                "identity": {
                    **call.context.scope.model_dump(),
                    "client_id": call.context.client_id,
                    "actor_id": call.context.actor_id,
                    "actions": sorted(call.context.granted_actions),
                },
                "request_id": call.context.request_id,
                "run_id": call.run_id,
                "contract_version": call.connection["contract_version"],
            }
        )
        try:
            async with asyncio.timeout(15):
                status, received, data = await self.send(target, headers, body)
        except (TimeoutError, httpcore.TimeoutException):
            raise business_error("BUSINESS_TIMEOUT") from None
        except (OSError, httpcore.NetworkError, httpcore.ProtocolError):
            raise business_error("BUSINESS_UNAVAILABLE") from None
        source_status(status)
        if (
            received.get("content-type", "").split(";", 1)[0].lower() != "application/json"
            or received.get("content-encoding", "identity") != "identity"
        ):
            raise business_error("BUSINESS_CONTRACT_CHANGED")
        try:
            value = json.loads(data)
            if isinstance(value, dict) and value.get("error") in {
                "BUSINESS_NO_DATA",
                "BUSINESS_UNLISTED",
                "BUSINESS_NOT_FOUND",
                "BUSINESS_FORBIDDEN",
                "BUSINESS_UNSUPPORTED",
                "BUSINESS_CONTRACT_CHANGED",
            }:
                raise business_error(value["error"])
            result = BusinessResult.model_validate(value)
            mapping = call.connection.get("field_mapping", {})
            return result.model_copy(
                update={"items": [map_fields(item, mapping) for item in result.items]}
            )
        except (ValueError, ValidationError, UnicodeError):
            raise business_error("BUSINESS_CONTRACT_CHANGED") from None

    async def send(
        self, target: ValidatedTarget, headers: dict[str, str], body: bytes
    ) -> tuple[int, dict[str, str], bytes]:
        async with httpcore.AsyncConnectionPool(
            network_backend=PinnedBackend(target), retries=0
        ) as pool:
            async with pool.stream(
                "POST", target.url, headers=list(headers.items()), content=body
            ) as response:
                received = {
                    k.decode("ascii").lower(): v.decode("latin1") for k, v in response.headers
                }
                chunks = bytearray()
                async for chunk in response.aiter_stream():
                    if len(chunks) + len(chunk) > 2 * 1024 * 1024:
                        raise business_error("BUSINESS_CONTRACT_CHANGED")
                    chunks.extend(chunk)
                return response.status, received, bytes(chunks)

    async def dictionary(self, call: BusinessCall) -> BusinessResult:
        return await self.invoke(call)

    async def candidates(self, call: BusinessCall) -> BusinessResult:
        return await self.invoke(call)

    async def quote(self, call: BusinessCall) -> BusinessResult:
        return await self.invoke(call)

    async def recheck(self, call: BusinessCall) -> BusinessResult:
        return await self.invoke(call)

    async def risk_facts(self, call: BusinessCall) -> BusinessResult:
        return await self.invoke(call)

    async def policies(self, call: BusinessCall) -> BusinessResult:
        return await self.invoke(call)

    async def metric_catalog(self, call: BusinessCall) -> BusinessResult:
        return await self.invoke(call)

    async def metric_results(self, call: BusinessCall) -> BusinessResult:
        return await self.invoke(call)
