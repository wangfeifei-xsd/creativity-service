"""导出运行与流式路由、管理详情及内部交接契约。"""

import argparse
import json
from pathlib import Path

from fastapi import FastAPI

from creativity_service.modules.runs.api import admin_router, router
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
    app = FastAPI(title="运行受理接口交接", version="1.0.0")
    app.include_router(router, prefix="/api/v1")
    app.include_router(admin_router, prefix="/admin/v1")
    outputs = {
        "openapi.json": app.openapi(),
        **{
            f"{model.__name__}.schema.json": model.model_json_schema(mode="serialization")
            for model in (
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
        },
    }
    root = Path("contracts/runs")
    for name, schema in outputs.items():
        path = root / name
        content = json.dumps(schema, ensure_ascii=False, indent=2) + "\n"
        if args.check:
            if not path.exists() or path.read_text() != content:
                raise SystemExit(f"运行契约过期：{name}")
        else:
            root.mkdir(exist_ok=True)
            path.write_text(content)


if __name__ == "__main__":
    main()
