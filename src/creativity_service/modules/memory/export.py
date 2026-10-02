"""导出记忆与运行、发布、删除模块的交接契约。"""

import argparse
import json
from pathlib import Path

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
    root = Path("contracts/memory")
    for model in (
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
    ):
        path = root / f"{model.__name__}.schema.json"
        content = (
            json.dumps(model.model_json_schema(mode="serialization"), ensure_ascii=False, indent=2)
            + "\n"
        )
        if args.check:
            if not path.exists() or path.read_text() != content:
                raise SystemExit(f"记忆契约过期：{path.name}")
        else:
            root.mkdir(exist_ok=True)
            path.write_text(content)


if __name__ == "__main__":
    main()
