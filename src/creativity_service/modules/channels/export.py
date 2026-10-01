"""离线导出渠道跨模块契约，后续任务与保留模块按此消费。"""

import argparse
import json
from pathlib import Path

from creativity_service.modules.channels.schemas import LifecycleEvent, UsageQuery, UsageView


def main() -> None:
    parser = argparse.ArgumentParser(description="导出渠道交接契约")
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    root = Path("contracts/channels")
    if not args.check:
        root.mkdir(exist_ok=True)
    for model in (LifecycleEvent, UsageQuery, UsageView):
        path = root / f"{model.__name__}.schema.json"
        value = (
            json.dumps(model.model_json_schema(mode="serialization"), ensure_ascii=False, indent=2)
            + "\n"
        )
        if args.check:
            if not path.exists() or path.read_text() != value:
                raise SystemExit(f"渠道契约过期：{path.name}")
        else:
            path.write_text(value)


if __name__ == "__main__":
    main()
