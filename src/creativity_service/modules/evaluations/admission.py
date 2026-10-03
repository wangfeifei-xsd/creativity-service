"""评测子任务受理与占位记录原子关联，取消锁阻止迟到派发。"""

import copy
from typing import Any

from creativity_service.core.context import AuthContext
from creativity_service.core.database import UnitOfWork
from creativity_service.core.deletion import ContentRef, DeletionGuard
from creativity_service.core.locking import ResourceKey
from creativity_service.core.primitives import RunInput, ServiceError
from creativity_service.modules.agents.runtime import AgentRunResolver
from creativity_service.modules.agents.schemas import FrozenExecutionSpec
from creativity_service.modules.evaluations.repositories import keys, repository, required
from creativity_service.modules.runs.schemas import ResolvedDefinition
from creativity_service.modules.runtime.admission import FrozenResolver, with_estimate


class EvaluationResolver(FrozenResolver):
    def __init__(
        self,
        service: Any,
        context: AuthContext,
        entry: dict[str, Any],
        spec: FrozenExecutionSpec,
        values: dict[str, Any],
    ) -> None:
        definition = with_estimate(AgentRunResolver.definition(spec), values)
        definition = definition.model_copy(
            update={"source_refs": (("evaluation_result", entry["id"]),)}
        )
        super().__init__(service.agents, definition)
        self.service, self.entry = service, entry

    def keys(self, context: AuthContext, definition: ResolvedDefinition) -> list[ResourceKey]:
        return super().keys(context, definition) + keys(
            context, [("evaluation_results", self.entry["id"])]
        )

    async def validate_in(
        self, uow: UnitOfWork, context: AuthContext, definition: ResolvedDefinition
    ) -> None:
        assert definition.frozen_spec is not None
        expected = AgentRunResolver.definition(definition.frozen_spec)
        await super().validate_in(
            uow, context, definition.model_copy(update={"source_refs": expected.source_refs})
        )
        task = await required(
            uow.connection, context.scope, "evaluations", self.entry["evaluation_id"]
        )
        entry = await required(
            uow.connection, context.scope, "evaluation_results", self.entry["id"]
        )
        if task["state"] != "RUNNING" or entry["state"] != "DISPATCHING" or entry["run_id"]:
            raise ServiceError("EVALUATION_DISPATCH_STOPPED", "评测已暂停、取消或样本已派发", 409)
        if (
            definition.frozen_spec.purpose != "evaluation"
            or definition.frozen_spec.snapshot_id != entry["candidate_id"]
        ):
            raise ServiceError("SNAPSHOT_INVALID", "评测候选不匹配", 409)
        await DeletionGuard(context.scope).check(
            uow, [ContentRef("evaluation_result", entry["id"])]
        )
        case = await required(uow.connection, context.scope, "evaluation_cases", entry["case_id"])
        if not await self.service.valid_case(uow, context, case):
            raise ServiceError("CONTENT_DELETED", "样本来源已删除，不能派发", 410)

    async def admitted_in(
        self,
        uow: UnitOfWork,
        context: AuthContext,
        definition: ResolvedDefinition,
        row: dict[str, Any],
    ) -> None:
        current = await required(
            uow.connection, context.scope, "evaluation_results", self.entry["id"]
        )
        await repository("evaluation_results", context.scope).change(
            uow, current["id"], current["revision"], {"run_id": row["id"], "state": "RUNNING"}
        )


async def dispatch(
    service: Any,
    context: AuthContext,
    entry: dict[str, Any],
    candidate: dict[str, Any],
    case: dict[str, Any],
) -> None:
    spec = await service.agents.load_candidate(context, candidate["snapshot_id"])
    run_service = copy.copy(service.runs)
    values = {**case["payload"]["context"], **case["payload"]["input"]}
    run_service.resolver = EvaluationResolver(service, context, entry, spec, values)
    await run_service.admit_run(
        context, RunInput(agent_code=spec.agent_code, input=values), "evaluation_" + entry["id"]
    )
