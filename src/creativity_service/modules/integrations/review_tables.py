"""主体复核新增模型单独冻结，不改变旧迁移使用的建表入口。"""

import json
from pathlib import Path

from sqlalchemy import Column, Index, MetaData, Table

from creativity_service.core.database.tables import column_type

BASELINE = json.loads(Path(__file__).with_name("baseline_v0021_review.json").read_text())


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
