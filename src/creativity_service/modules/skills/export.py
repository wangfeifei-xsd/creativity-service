"""导出供 Agent 发布与运行编排消费的技能加载契约。"""

import argparse
import json
from pathlib import Path

from creativity_service.modules.skills.schemas import (
    SkillDependencySummary,
    SkillLoadRequest,
    SkillLoadResult,
    SkillSettings,
    SkillTestView,
)


def main() -> None:
    parser = argparse.ArgumentParser(description="导出技能包交接契约")
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    root = Path("contracts/skills")
    if not args.check:
        root.mkdir(exist_ok=True)
    for model in (
        SkillDependencySummary,
        SkillLoadRequest,
        SkillLoadResult,
        SkillSettings,
        SkillTestView,
    ):
        path = root / f"{model.__name__}.schema.json"
        value = (
            json.dumps(model.model_json_schema(mode="serialization"), ensure_ascii=False, indent=2)
            + "\n"
        )
        if args.check:
            if not path.exists() or path.read_text() != value:
                raise SystemExit(f"技能契约过期：{path.name}")
        else:
            path.write_text(value)


if __name__ == "__main__":
    main()
