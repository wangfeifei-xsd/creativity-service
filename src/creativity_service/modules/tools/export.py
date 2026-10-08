"""离线导出工具绑定、运行授权与统一执行入口契约。"""

import argparse
from pathlib import Path

from creativity_service.core.contracts.files import schema_bundle, write_contract
from creativity_service.integrations.tools import AdapterResult
from creativity_service.modules.tools.schemas import RunToolGrant, ToolDefinition, ToolExecution


def main() -> None:
    parser = argparse.ArgumentParser(description="导出工具交接契约")
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    write_contract(
        Path("contracts/internal/tools.json"),
        schema_bundle(
            (AdapterResult, RunToolGrant, ToolDefinition, ToolExecution), mode="serialization"
        ),
        check=args.check,
    )


if __name__ == "__main__":
    main()
