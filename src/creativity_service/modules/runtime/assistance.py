"""内置配置助手复用模型执行、计费与租约，候选业务校验失败时有限修复。"""

import asyncio
import json
from typing import Any, cast

from pydantic import ValidationError

from creativity_service.core.context import AuthContext
from creativity_service.core.contracts import BusinessResult
from creativity_service.core.primitives import ServiceError
from creativity_service.modules.agents.assistance_schemas import AssistanceOutput
from creativity_service.modules.agents.schemas import FrozenExecutionSpec
from creativity_service.modules.models.schemas import FrozenModel
from creativity_service.modules.runs.schemas import Lease
from creativity_service.modules.runtime.engine import RuntimeExecutor


async def execute_assistance(
    engine: RuntimeExecutor,
    context: AuthContext,
    lease: Lease,
    spec: FrozenExecutionSpec,
    descriptor: dict[str, Any],
    values: dict[str, Any],
) -> None:
    service = engine.assistance
    if service is None:
        raise ServiceError("ASSISTANCE_UNAVAILABLE", "智能协助尚未装配", 503)
    messages = [
        {"role": "system", "content": spec.definition.instructions},
        {"role": "user", "content": json.dumps(values, ensure_ascii=False)},
    ]
    if (
        len(json.dumps(messages, ensure_ascii=False).encode())
        > spec.definition.context.context_limit * 4
    ):
        raise ServiceError("CONTEXT_LIMIT_EXCEEDED", "需求和资源目录超过所选模型的上下文容量", 422)
    route = next(
        v for v in spec.versions if v.version_id == spec.definition.bindings.model_route_version
    )
    models = [
        FrozenModel.model_validate(value)
        for value in cast(list[dict[str, Any]], route.content["models"])
    ]
    parameters = {}
    # 完整方案重复包含步骤与最终输出结构，还需容纳推理 Token；仅覆盖本次请求，仍走预算预占。
    if models and all("max_tokens" in model.parameter_allowlist for model in models):
        parameters["max_tokens"] = max(
            16384, *(int(model.parameters.get("max_tokens", 0)) for model in models)
        )
    for attempt in range(2):
        await service.require(context, descriptor.get("agent_id"))
        async with asyncio.timeout(spec.definition.steps[0].timeout_seconds):
            value = await engine.model(
                context,
                lease,
                spec,
                spec.definition.steps[0],
                "generate" if attempt == 0 else "generate.i1",
                messages,
                parameters=parameters,
            )
        try:
            output = AssistanceOutput.model_validate(value["structured"])
            await service.validate(context, output, descriptor, values)
        except (ServiceError, ValidationError) as exc:
            if isinstance(exc, ServiceError) and exc.status in {403, 404}:
                raise
            if attempt:
                reason = (
                    exc.message if isinstance(exc, ServiceError) else "候选配置不符合输入输出契约"
                )
                raise ServiceError("ASSISTANCE_INVALID", "方案未通过校验：" + reason, 422) from exc
            reason = exc.message if isinstance(exc, ServiceError) else "候选配置不符合输入输出契约"
            messages += [
                {
                    "role": "assistant",
                    "content": json.dumps(value["structured"], ensure_ascii=False),
                },
                {"role": "user", "content": "请修正以下配置问题，无法满足时追问用户：" + reason},
            ]
            continue
        await engine.runs.finish_run(
            lease,
            "SUCCEEDED",
            BusinessResult.model_validate(output.model_dump(mode="json", by_alias=True)),
        )
        return
