"""发布门禁只读取当前事实；预算检查不产生预占，正式受理仍须重新准入。"""

from decimal import Decimal
from typing import Any

from creativity_service.core.context import AuthContext
from creativity_service.core.database import UnitOfWork
from creativity_service.core.primitives import ServiceError, utcnow
from creativity_service.modules.agents.schemas import AgentDefinition
from creativity_service.modules.budgets.services import BudgetService, matches
from creativity_service.modules.models.schemas import FrozenModel
from creativity_service.modules.releases.ports import EvaluationEvidence
from creativity_service.modules.usage.schemas import AttemptPlan


async def check_budget(
    service: BudgetService,
    uow: UnitOfWork,
    context: AuthContext,
    agent_id: str,
    definition: AgentDefinition,
    models: list[FrozenModel],
) -> list[dict[str, Any]]:
    service.require(uow, context)
    policies = await service.policies(uow)
    relevant: dict[str, dict[str, Any]] = {}
    for model in models:
        plan = AttemptPlan(
            run_id="publication",
            attempt_id="publication",
            agent_id=agent_id,
            model_id=model.model_id,
            connection_id=model.connection_id,
            purpose="production",
            input_tokens=definition.context.context_limit,
            max_output_tokens=definition.limits.token_limit - definition.context.context_limit,
        )
        _, price, upper = await service.estimate(uow, plan)
        ceiling = definition.limits.cost_limit
        if ceiling and (
            not price
            or upper is None
            or price["currency"] != ceiling.currency
            or upper > ceiling.amount
        ):
            raise ServiceError(
                "BUDGET_NOT_EXECUTABLE", "费用上限缺少有效价格或不足以执行一次最大调用", 422
            )
        for policy in policies:
            if not matches(policy, service.snapshot(context, plan)):
                continue
            relevant[policy["id"]] = {
                k: policy[k]
                for k in ("id", "version_id", "scope_type", "scope_id", "unit", "mode", "currency")
            }
            relevant[policy["id"]]["limit_value"] = str(policy["limit_value"])
            if policy["mode"] != "HARD":
                continue
            if policy["unit"] == "amount":
                if not price or upper is None or price["currency"] != policy["currency"]:
                    raise ServiceError("BUDGET_NOT_EXECUTABLE", "金额硬预算缺少有效模型价格", 422)
                increment = ceiling.amount if ceiling else upper
            elif policy["unit"] == "tokens":
                increment = Decimal(definition.limits.token_limit)
            else:
                increment = Decimal(1)
            if await service.exposure(uow, policy, utcnow()) + increment > policy["limit_value"]:
                raise ServiceError("BUDGET_NOT_EXECUTABLE", "当前可用预算不足以执行此配置", 422)
    return sorted(relevant.values(), key=lambda p: p["id"])


def check_evidence(
    evidence: EvaluationEvidence | None,
    context: AuthContext,
    agent_id: str,
    content_digest: str,
    dependencies_digest: str,
    refs: tuple[str, ...],
) -> None:
    if evidence is None:
        raise ServiceError(
            "EVALUATION_REQUIRED", "生产发布需要有效评测报告，评测服务尚未提供证据", 422
        )
    if (
        evidence.channel_id != context.scope.channel_id
        or evidence.agent_id != agent_id
        or evidence.environment != context.scope.environment
        or not evidence.passed
        or evidence.expires_at.tzinfo is None
        or evidence.expires_at <= utcnow()
        or evidence.content_digest != content_digest
        or evidence.dependencies_digest != dependencies_digest
        or set(evidence.report_ids) != set(refs)
        or not refs
        or len(evidence.report_digest) != 64
    ):
        raise ServiceError(
            "EVALUATION_STALE", "评测未通过、已过期或报告与当前内容及依赖摘要不一致", 422
        )
