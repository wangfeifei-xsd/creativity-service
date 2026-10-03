"""导出样本装配、评测请求与报告契约，供导入方及验收脚本复用。"""

import argparse
import json
from pathlib import Path

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
    root = Path("contracts/evaluations")
    if not args.check:
        root.mkdir(parents=True, exist_ok=True)
    for model in (
        CaseInput,
        DatasetVersionInput,
        ImportInput,
        ImportPreview,
        RunCaseInput,
        EvaluationCreate,
        EvaluationReview,
        ComparisonReport,
    ):
        path = root / f"{model.__name__}.schema.json"
        content = json.dumps(model.model_json_schema(), ensure_ascii=False, indent=2) + "\n"
        if args.check:
            if not path.exists() or path.read_text() != content:
                raise SystemExit(f"评测契约过期：{path}")
        else:
            path.write_text(content)


if __name__ == "__main__":
    main()
