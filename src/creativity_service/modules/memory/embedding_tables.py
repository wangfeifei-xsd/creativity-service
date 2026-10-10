"""记忆向量冻结模型，迁移与运行共用本次字段定义。"""

import json
from pathlib import Path

from sqlalchemy import Column, Index, MetaData

from creativity_service.core.database.soft_delete import soft_delete_table
from creativity_service.core.database.tables import column_type

BASELINE = json.loads(
    Path(__file__).with_name("embedding_tables_v0041_0.json").read_text(encoding="utf-8")
)


def build_metadata() -> MetaData:
    result = MetaData()
    for definition in BASELINE:
        table = soft_delete_table(
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


metadata = build_metadata()
