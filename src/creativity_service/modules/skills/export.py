"""导出供 Agent 发布与运行编排消费的技能加载契约。"""

import argparse
from pathlib import Path

from creativity_service.core.contracts.files import schema_bundle, write_contract
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
    write_contract(
        Path("contracts/internal/skills.json"),
        schema_bundle(
            (
                SkillDependencySummary,
                SkillLoadRequest,
                SkillLoadResult,
                SkillSettings,
                SkillTestView,
            ),
            mode="serialization",
        ),
        check=args.check,
    )


if __name__ == "__main__":
    main()
