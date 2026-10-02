"""导出 08 内部调用及管理查询契约，不作为外部认证上下文入口。"""

import argparse
import json
from pathlib import Path

from creativity_service.modules.budgets.schemas import BudgetCreate, BudgetView
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
    root = Path("contracts/usage")
    if not args.check:
        root.mkdir(exist_ok=True)
    for model in (
        AttemptPlan,
        ReservationReceipt,
        UsageFilter,
        UsageSummary,
        BudgetCreate,
        BudgetView,
    ):
        path = root / f"{model.__name__}.schema.json"
        content = (
            json.dumps(model.model_json_schema(mode="serialization"), ensure_ascii=False, indent=2)
            + "\n"
        )
        if args.check:
            if not path.exists() or path.read_text() != content:
                raise SystemExit(f"用量预算契约过期：{path.name}")
        else:
            path.write_text(content)


if __name__ == "__main__":
    main()
