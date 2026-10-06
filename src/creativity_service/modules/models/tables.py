"""模型模块冻结定义，迁移只读取本版本文件。"""

import json
from pathlib import Path

from sqlalchemy import Column, Index, MetaData, Table
from sqlalchemy.dialects.postgresql import JSONB

from creativity_service.core.database.tables import column_type

BASELINE = json.loads(Path(__file__).with_name("baseline_v0004.json").read_text(encoding="utf-8"))


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
metadata.tables["model_connections"].append_column(
    Column(
        "allowed_networks",
        JSONB(),
        nullable=True,
        comment="连接允许的 IP 网段，空数组仅允许公网",
        info={"required": True, "source": "连接管理页经服务层校验", "sensitivity": "内部"},
    )
)
