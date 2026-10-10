"""有界导入预览保留原行号，错误只反馈字段及原因，不回显敏感原文。"""

import csv
import io
import json
from typing import Any

from pydantic import ValidationError

from creativity_service.core.primitives import ServiceError, digest
from creativity_service.modules.evaluations.schemas import (
    CaseInput,
    ImportInput,
    ImportPreview,
    ImportRow,
)


def preview(body: ImportInput) -> ImportPreview:
    rows = []
    columns: set[str] = set()
    records: list[tuple[int, Any]] = []
    if body.format == "jsonl":
        for number, line in enumerate(body.content.splitlines(), 1):
            try:
                records.append((number, json.loads(line)))
            except ValueError:
                records.append((number, None))
    else:
        try:
            reader = csv.DictReader(io.StringIO(body.content), strict=True)
            headers = reader.fieldnames or []
            if not headers or len(set(headers)) != len(headers):
                raise ServiceError("IMPORT_INVALID", "CSV 表头为空或重复", 422)
            records = []
            number = 2
            for row in reader:
                records.append((number, row))
                number = reader.line_num + 1
        except csv.Error:
            raise ServiceError("IMPORT_INVALID", "CSV 格式不合法", 422) from None
    if not records or len(records) > 1000:
        raise ServiceError("IMPORT_LIMIT", "每次导入需要 1 至 1000 条样本", 422)
    seen: set[str] = set()
    for number, raw in records:
        case = None
        errors = []
        try:
            if not isinstance(raw, dict) or None in raw:
                raise ValueError("行结构不合法")
            columns.update(raw)
            mapped = {body.mapping.get(k, k): v for k, v in raw.items()}
            if len(mapped) != len(raw):
                raise ValueError("多个来源列映射到同一字段")
            if body.format == "csv":
                for key in (
                    "input",
                    "context",
                    "assertions",
                    "labels",
                    "human_label",
                    "source_refs",
                    "fixture",
                ):
                    if key in mapped:
                        if mapped[key]:
                            mapped[key] = json.loads(mapped[key])
                        else:
                            mapped.pop(key)
                if mapped.get("expected_error") == "":
                    mapped.pop("expected_error")
            case = CaseInput.model_validate(mapped)
            if case.case_key in seen:
                raise ValueError("样本定位键重复")
            seen.add(case.case_key)
        except ValidationError as exc:
            for error in exc.errors(include_input=False):
                path = ".".join(str(value) for value in error["loc"])
                reason = str(error.get("ctx", {}).get("error", ""))
                # 只展示已知固定文案，其他校验不回显样本原文。
                if not path and reason in {
                    "输入与上下文字段不能重名",
                    "样本需要确定性断言、预期拒绝或人工判定标准",
                }:
                    errors.append(reason)
                else:
                    errors.append(f"{path or '样本'}：字段格式不合法")
        except (TypeError, ValueError):
            errors = ["行格式、字段映射或样本定位键不合法"]
        rows.append(ImportRow(row_number=number, case=case if not errors else None, errors=errors))
    return ImportPreview(
        columns=sorted(columns),
        mapping=body.mapping,
        rows=rows,
        valid_count=sum(not r.errors for r in rows),
        error_count=sum(bool(r.errors) for r in rows),
        preview_digest=digest(body.model_dump(mode="json", exclude={"commit", "preview_digest"})),
    )
