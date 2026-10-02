"""十进制计价；嵌套维度按覆盖关系扣除，未知量绝不当作零。"""

from collections.abc import Mapping
from decimal import ROUND_CEILING, ROUND_HALF_UP, Decimal
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from creativity_service.core.primitives import ServiceError
from creativity_service.modules.usage.schemas import PriceCreate, PriceItem

PRECISION = Decimal("0.00000001")


def timezone(name: str) -> ZoneInfo:
    try:
        return ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError) as exc:
        raise ServiceError("VALIDATION_ERROR", "时区无效", 422) from exc


def validate_relations(relations: dict[str, str]) -> None:
    for child in relations:
        seen = {child}
        parent = relations[child]
        while True:
            if parent in seen:
                raise ServiceError("USAGE_INVALID", "计量子集关系不能循环", 422)
            seen.add(parent)
            if parent not in relations:
                break
            parent = relations[parent]


def validate_price(body: PriceCreate) -> None:
    validate_relations(body.subset_relations)
    dimensions = [p.dimension for p in body.items]
    if len(set(dimensions)) != len(dimensions) or not body.name.strip() or not body.source.strip():
        raise ServiceError("PRICE_INVALID", "价格维度不能重复，名称与来源不能为空", 422)
    for child, parent in body.subset_relations.items():
        if child not in dimensions or parent not in dimensions:
            raise ServiceError("PRICE_INVALID", "价格须包含子集与所属总量维度", 422)


def normalize(tokens: Mapping[str, int | None], relations: dict[str, str]) -> None:
    validate_relations(relations)
    if any(v is not None and (type(v) is not int or v < 0) for v in tokens.values()):
        raise ServiceError("USAGE_INVALID", "用量须为非负整数或缺失值", 422)
    for parent in set(relations.values()):
        children = [tokens.get(k) for k, p in relations.items() if p == parent]
        total = tokens.get(parent)
        if total is not None and sum(v for v in children if v is not None) > total:
            raise ServiceError("USAGE_INVALID", "子集用量超过所属总量", 422)


def calculate(
    price: dict[str, Any] | None,
    tokens: Mapping[str, int | None],
    relations: dict[str, str],
    status: str,
    *,
    upper: bool = False,
) -> tuple[Decimal | None, str, dict[str, Any]]:
    normalize(tokens, relations)
    if price is None or status == "MISSING":
        return None, "UNPRICED", {"reason": "价格或用量尚未确认"}
    declared = price["subset_relations"]
    if declared != relations:
        return None, "UNPRICED", {"reason": "价格与供应商的计量子集声明不一致"}
    items = [PriceItem.model_validate(item) for item in price["price_items"]]
    dimensions = {p.dimension for p in items}
    # 未单列价格的子集可由父维度覆盖；独立维度缺价必须保留未定价。
    for dimension, count in tokens.items():
        parent = dimension
        while parent not in dimensions and parent in relations:
            parent = relations[parent]
        if count and parent not in dimensions:
            return None, "UNPRICED", {"reason": "存在未配置价格的计量维度"}
    lines: list[dict[str, Any]] = []
    total = Decimal(0)
    for item in items:
        quantity = tokens.get(item.dimension)
        children = [k for k, p in relations.items() if p == item.dimension and k in dimensions]
        if quantity is None or any(tokens.get(k) is None for k in children):
            return None, "UNPRICED", {"reason": "计价维度用量缺失"}
        net = quantity - sum(tokens[k] or 0 for k in children)
        rate = item.amount / item.per_units
        if upper:
            # 上限尚不知缓存/推理分布时使用所属树的最高单价，避免优惠或嵌套高价低估。
            def belongs(dimension: str, root: str = item.dimension) -> bool:
                while dimension in relations:
                    dimension = relations[dimension]
                    if dimension == root:
                        return True
                return False

            rate = max([rate] + [p.amount / p.per_units for p in items if belongs(p.dimension)])
        subtotal = Decimal(net) * rate
        total += subtotal
        lines.append(
            {
                "dimension": item.dimension,
                "quantity": quantity,
                "excluded_subsets": children,
                "billable_units": net,
                "rate": str(rate),
                "amount": str(subtotal),
            }
        )
    result = total.quantize(PRECISION, rounding=ROUND_CEILING if upper else ROUND_HALF_UP)
    if result >= Decimal("10000000000000000"):
        raise ServiceError("USAGE_INVALID", "核算金额超出支持范围", 422)
    return (
        result,
        "PRICED" if status == "REPORTED" else "PROVISIONAL",
        {
            "price_version_id": price["id"],
            "source": price["source"],
            "formula": lines,
            "rounding": "上界进位" if upper else "小数八位四舍五入",
        },
    )
