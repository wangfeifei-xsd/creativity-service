"""导出记忆与运行、发布、删除模块的交接契约。"""

import argparse
from pathlib import Path

from creativity_service.core.contracts.files import schema_bundle, write_contract
from creativity_service.modules.memory.schemas import (
    CandidateInput,
    MemoryCreate,
    MemoryDeletion,
    MemoryDetail,
    MemoryList,
    MemoryLoad,
    MemoryPolicy,
    MemorySelection,
    MemoryUpdate,
    PreferenceInput,
    SourceInput,
)


def main() -> None:
    parser = argparse.ArgumentParser(description="导出记忆契约")
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    write_contract(
        Path("contracts/internal/memory.json"),
        schema_bundle(
            (
                CandidateInput,
                MemoryCreate,
                MemoryDetail,
                MemoryList,
                MemoryLoad,
                MemoryPolicy,
                MemorySelection,
                MemoryUpdate,
                MemoryDeletion,
                PreferenceInput,
                SourceInput,
            ),
            mode="serialization",
        ),
        check=args.check,
    )


if __name__ == "__main__":
    main()
