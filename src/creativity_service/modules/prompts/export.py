"""导出供 16/17 消费的冻结草稿、渲染及证据契约。"""

import argparse
from pathlib import Path

from creativity_service.core.contracts.files import schema_bundle, write_contract
from creativity_service.modules.prompts.schemas import (
    PromptDebugDescriptor,
    PromptDebugEvidence,
    PromptDebugRun,
    PromptDependencySummary,
    PromptPortable,
    PromptRuntimeInput,
)


def main() -> None:
    parser = argparse.ArgumentParser(description="导出提示词交接契约")
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    write_contract(
        Path("contracts/internal/prompts.json"),
        schema_bundle(
            (
                PromptDebugDescriptor,
                PromptDebugEvidence,
                PromptDebugRun,
                PromptDependencySummary,
                PromptPortable,
                PromptRuntimeInput,
            ),
            mode="serialization",
        ),
        check=args.check,
    )


if __name__ == "__main__":
    main()
