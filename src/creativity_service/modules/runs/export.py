"""导出运行内部交接契约，HTTP 与流式接口以统一 OpenAPI 为准。"""

import argparse
from pathlib import Path

from creativity_service.core.contracts.files import schema_bundle, write_contract
from creativity_service.modules.runs.schemas import (
    AdmissionReceipt,
    ExecutionPolicy,
    Lease,
    RerunInput,
    ResolvedDefinition,
    RunDetail,
    RunFilterOptions,
    RunRequest,
    RunSummary,
    TracePage,
)


def main() -> None:
    parser = argparse.ArgumentParser(description="导出运行交接契约")
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    write_contract(
        Path("contracts/internal/runs.json"),
        schema_bundle(
            (
                RunRequest,
                AdmissionReceipt,
                ExecutionPolicy,
                Lease,
                ResolvedDefinition,
                RerunInput,
                RunDetail,
                RunFilterOptions,
                RunSummary,
                TracePage,
            )
        ),
        check=args.check,
    )


if __name__ == "__main__":
    main()
