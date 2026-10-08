"""导出样本装配、评测请求与报告契约，供导入方及验收脚本复用。"""

import argparse
from pathlib import Path

from creativity_service.core.contracts.files import schema_bundle, write_contract
from creativity_service.modules.evaluations.schemas import (
    CaseInput,
    ComparisonReport,
    DatasetVersionInput,
    EvaluationCreate,
    EvaluationReview,
    ImportInput,
    ImportPreview,
    RunCaseInput,
)


def main() -> None:
    parser = argparse.ArgumentParser(description="导出评测契约")
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    write_contract(
        Path("contracts/internal/evaluations.json"),
        schema_bundle(
            (
                CaseInput,
                DatasetVersionInput,
                ImportInput,
                ImportPreview,
                RunCaseInput,
                EvaluationCreate,
                EvaluationReview,
                ComparisonReport,
            ),
            mode="validation",
        ),
        check=args.check,
    )


if __name__ == "__main__":
    main()
