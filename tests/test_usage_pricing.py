"""USG-F02/F05/F06：计价纯函数、供应商子集及业务周期边界。"""

from datetime import UTC, datetime
from decimal import Decimal

import pytest

from creativity_service.core.primitives import ServiceError
from creativity_service.modules.budgets.services import period_start
from creativity_service.modules.usage.pricing import calculate


def test_nested_cached_and_reasoning_not_added_twice():
    price = {
        "id": "p1",
        "source": "供应商价格",
        "subset_relations": {"cached": "input", "reasoning": "output"},
        "price_items": [
            {"dimension": "input", "amount": "1", "per_units": 100},
            {"dimension": "cached", "amount": "0.1", "per_units": 100},
            {"dimension": "output", "amount": "2", "per_units": 100},
            {"dimension": "reasoning", "amount": "3", "per_units": 100},
        ],
    }
    cost, status, formula = calculate(
        price,
        {"input": 100, "cached": 20, "output": 50, "reasoning": 10},
        price["subset_relations"],
        "REPORTED",
    )
    assert cost == Decimal("1.92")
    assert status == "PRICED"
    upper, _, _ = calculate(
        price,
        {"input": 100, "cached": 0, "output": 50, "reasoning": 0},
        price["subset_relations"],
        "ESTIMATED",
        upper=True,
    )
    assert upper == Decimal("2.5")
    assert formula["formula"][0]["billable_units"] == 80
    assert calculate(price, {"input": None}, price["subset_relations"], "ESTIMATED")[0] is None
    assert calculate(None, {"input": 100, "output": 10}, {}, "REPORTED")[1] == "UNPRICED"
    with pytest.raises(ServiceError):
        calculate(price, {"input": 10, "cached": 20}, price["subset_relations"], "REPORTED")


def test_business_timezone_month_and_dst_boundaries():
    at = datetime(2026, 9, 30, 17, tzinfo=UTC)
    assert period_start(at, "month", "Asia/Shanghai") == datetime(2026, 9, 30, 16, tzinfo=UTC)
    assert period_start(at, "month", "UTC") == datetime(2026, 9, 1, tzinfo=UTC)
    assert period_start(datetime(2026, 3, 8, 8, tzinfo=UTC), "day", "America/New_York") == datetime(
        2026, 3, 8, 5, tzinfo=UTC
    )
