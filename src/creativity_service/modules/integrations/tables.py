"""身份委托模块冻结模型，迁移与运行共用本次字段定义。"""

import json
from pathlib import Path

from sqlalchemy import Column, Index, MetaData, Table

from creativity_service.core.database.tables import column_type
from creativity_service.modules.integrations.review_tables import build_metadata as review_metadata

BASELINE = json.loads(Path(__file__).with_name("tables_v0046_0.json").read_text(encoding="utf-8"))


def build_metadata() -> MetaData:
    result = MetaData()
    for definition in BASELINE:
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


metadata = build_metadata()
for review_table in review_metadata().tables.values():
    review_table.to_metadata(metadata)
