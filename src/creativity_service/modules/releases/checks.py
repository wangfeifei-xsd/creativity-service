"""发布门禁只读取当前事实；预算检查不产生预占，正式受理仍须重新准入。"""

from decimal import Decimal
from typing import Any

from sqlalchemy import select

from creativity_service.core.context import AuthContext
from creativity_service.core.database import UnitOfWork
from creativity_service.core.database.tables import metadata
from creativity_service.core.primitives import ServiceError, digest, utcnow
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
    *,
    defer_exposure: bool = False,
) -> list[dict[str, Any]]:
    if defer_exposure:
        service.require_configuration(uow, context)
    else:
        service.require(uow, context, read_only=True)
    policies = await service.policies(uow)
    now = utcnow()
    prices = (
        await service.prices(uow, [model.model_id for model in models], now)
        if definition.limits.cost_limit or any(p["unit"] == "amount" for p in policies)
        else {}
    )
    exposure = {} if defer_exposure else await service.exposure_data(uow, policies, now)
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
        _, price, upper = await service.estimate(uow, plan, prices=prices)
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
            # 调用 Key 和渠道并发均为实时准入条件，不改变 Agent 的执行内容或发布证据。
            if policy["scope_type"] != "key" and not (
                policy["scope_type"] == "channel" and policy["unit"] == "concurrency"
            ):
                relevant[policy["id"]] = {
                    k: policy[k]
                    for k in (
                        "id",
                        "version_id",
                        "scope_type",
                        "scope_id",
                        "unit",
                        "mode",
                        "currency",
                    )
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
            if defer_exposure:
                # 静态配置受共享配置锁保护；动态占用留到受理事务末尾的账本锁内核对。
                uow.read_cache.setdefault("admission-budget-demands", []).append(
                    (policy, increment)
                )
            elif (
                await service.exposure(uow, policy, now, exposure) + increment
                > policy["limit_value"]
            ):
                raise ServiceError("BUDGET_NOT_EXECUTABLE", "当前可用预算不足以执行此配置", 422)
    return sorted(relevant.values(), key=lambda p: p["id"])


async def published_dependencies_match(
    uow: UnitOfWork, version: dict[str, Any], manifest: dict[str, Any]
) -> bool:
    """兼容旧版将渠道并发写入摘要的发布记录，不修改不可变版本或放宽其他依赖。"""
    if digest(manifest) == version["dependencies_digest"]:
        return True
    # 仅不匹配的历史版本读取发布时的预算版本，每个策略最多返回一条。
    table = metadata.tables["resource_versions"]
    historical = (
        await uow.connection.execute(
            select(table.c.id, table.c.resource_id, table.c.content)
            .where(
                table.c.channel_id == uow.scope.channel_id,
                table.c.resource_type == "budget_policy",
                table.c.state == "FROZEN",
                table.c.created_at <= version["created_at"],
            )
            .distinct(table.c.resource_id)
            .order_by(table.c.resource_id, table.c.created_at.desc(), table.c.id.desc())
        )
    ).mappings()
    budgets = list(manifest["policies"]["budgets"])
    for row in historical:
        policy = row["content"]
        if (
            policy["scope_type"] == "channel"
            and policy["unit"] == "concurrency"
            and policy["status"] == "ACTIVE"
        ):
            budgets.append(
                {
                    **{
                        k: policy[k] for k in ("scope_type", "scope_id", "unit", "mode", "currency")
                    },
                    "id": row["resource_id"],
                    "version_id": row["id"],
                    # 原字段为 NUMERIC(24, 8)，还原旧摘要保留的八位小数。
                    "limit_value": format(Decimal(policy["limit_value"]), ".8f"),
                }
            )
    legacy = {
        **manifest,
        "policies": {**manifest["policies"], "budgets": sorted(budgets, key=lambda p: p["id"])},
    }
    return bool(digest(legacy) == version["dependencies_digest"])


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
