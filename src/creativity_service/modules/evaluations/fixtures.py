"""夹具只替换已授权工具的传输，执行次数、结构和证据仍走统一工具管线。"""

from typing import Any

from creativity_service.core.context import AuthContext
from creativity_service.core.database import transaction
from creativity_service.core.deletion import ContentRef, DeletionGuard
from creativity_service.core.primitives import ServiceError, digest
from creativity_service.integrations.tools import AdapterResult, ToolAdapterError
from creativity_service.modules.evaluations.datasets import DatasetService
from creativity_service.modules.evaluations.repositories import keys, repository, required
from creativity_service.modules.evaluations.schemas import FixtureCall
from creativity_service.modules.tools.schemas import ToolExecution


class FixtureProvider:
    def __init__(self, calls: list[FixtureCall]) -> None:
        self.calls = calls

    async def invoke(self, context: AuthContext, call: ToolExecution) -> AdapterResult:
        matches = [
            f
            for f in self.calls
            if f.tool_version_id == call.tool_version_id
            and digest(f.arguments) == digest(call.arguments)
        ]
        if len(matches) != 1:
            raise ServiceError(
                "EVALUATION_FIXTURE_MISSING", "固定数据缺少唯一匹配的工具版本及参数", 422
            )
        value = matches[0]
        if value.error_code:
            raise ToolAdapterError(value.error_code, "固定工具故障样本", retryable=False)
        return AdapterResult(
            data=value.data,
            source_request_id="fixture_" + digest(value.model_dump(mode="json"))[:40],
            source_version=value.source_version,
            observed_at=value.observed_at,
            coverage={"evaluation_fixture": True},
        )


async def provider_for(engine: Any, context: AuthContext, run_id: str) -> FixtureProvider | None:
    async with transaction(engine, context.scope, keys(context)) as uow:
        entries = await repository("evaluation_results", context.scope).find(
            uow.connection, run_id=run_id
        )
        if not entries:
            return None
        if len(entries) != 1:
            raise ServiceError("EVALUATION_RUN_INVALID", "评测运行关联不唯一", 503)
        entry = entries[0]
        task = await required(uow.connection, context.scope, "evaluations", entry["evaluation_id"])
        await DeletionGuard(context.scope).check(
            uow, [ContentRef("evaluation_result", entry["id"])]
        )
        if task["state"] not in {"RUNNING", "PAUSED"}:
            raise ServiceError("RUN_INACTIVE", "评测已停止", 409)
        if task["execution_mode"] == "live_readonly":
            return None
        case = await required(uow.connection, context.scope, "evaluation_cases", entry["case_id"])
        if not await DatasetService.valid_case(uow, context, case):
            raise ServiceError("CONTENT_DELETED", "工具夹具来源已删除", 410)
        calls = []
        if case["fixture_id"]:
            fixture = await required(
                uow.connection, context.scope, "evaluation_fixtures", case["fixture_id"]
            )
            if fixture["invalidated"] or fixture["payload"] is None:
                raise ServiceError("CONTENT_DELETED", "工具夹具来源已删除", 410)
            calls = [FixtureCall.model_validate(v) for v in fixture["payload"]]
        return FixtureProvider(calls)
