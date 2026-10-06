"""渠道模块冻结定义，迁移只读取本版本文件。"""

import json
from pathlib import Path
from typing import Any

from sqlalchemy import Column, Index, MetaData, Table

from creativity_service.core.database.tables import column_type

BASELINE = json.loads(Path(__file__).with_name("tables_v0041_0.json").read_text(encoding="utf-8"))
CURRENT = json.loads(Path(__file__).with_name("tables_v0041_1.json").read_text(encoding="utf-8"))


def build_metadata(definitions: list[dict[str, Any]] | None = None) -> MetaData:
    """无参数调用仍返回旧迁移定义，运行仓储显式选用当前版本。"""
    result = MetaData()
    for definition in BASELINE if definitions is None else definitions:
        table = Table(
            definition["name"],
            result,
            *(
                Column(
                    c["name"], column_type(c["type"]), nullable=True, comment=c["comment"], info=c
                )
                for c in definition["columns"]
            ),
            comment=definition["comment"],
            info=definition,
        )
        for number, columns in enumerate(definition["indexes"]):
            Index(f"ix_{table.name}_{number}", *(table.c[name] for name in columns))
    return result


metadata = build_metadata(CURRENT)
