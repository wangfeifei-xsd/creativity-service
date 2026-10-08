"""导出 08 内部调用及管理查询契约，不作为外部认证上下文入口。"""

import argparse
from pathlib import Path

from creativity_service.core.contracts.files import schema_bundle, write_contract
from creativity_service.modules.budgets.schemas import BudgetCreate, BudgetView, PlatformLimitView
from creativity_service.modules.usage.schemas import (
    AttemptPlan,
    ReservationReceipt,
    UsageFilter,
    UsageSummary,
)


def main() -> None:
    parser = argparse.ArgumentParser(description="导出用量预算交接契约")
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    write_contract(
        Path("contracts/internal/usage.json"),
        schema_bundle(
            (
                AttemptPlan,
                ReservationReceipt,
                UsageFilter,
                UsageSummary,
                BudgetCreate,
                BudgetView,
                PlatformLimitView,
            ),
            mode="serialization",
        ),
        check=args.check,
    )


if __name__ == "__main__":
    main()
