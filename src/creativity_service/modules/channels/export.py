"""离线导出渠道跨模块契约，后续任务与保留模块按此消费。"""

import argparse
from pathlib import Path

from creativity_service.core.contracts.files import schema_bundle, write_contract
from creativity_service.modules.channels.schemas import LifecycleEvent, UsageQuery, UsageView


def main() -> None:
    parser = argparse.ArgumentParser(description="导出渠道交接契约")
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    write_contract(
        Path("contracts/internal/channels.json"),
        schema_bundle((LifecycleEvent, UsageQuery, UsageView), mode="serialization"),
        check=args.check,
    )


if __name__ == "__main__":
    main()
