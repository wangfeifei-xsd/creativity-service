"""三层记忆的增量存储定义；早期迁移仍使用原冻结模型。"""

import json
from pathlib import Path
from typing import Any

from sqlalchemy import Column, Index, MetaData, Table

from creativity_service.core.database.tables import column_type

BASELINE = json.loads(Path(__file__).with_name("layer_tables_v0041_0.json").read_text())
COLUMNS: dict[str, list[dict[str, Any]]] = {
    "memory_policies": [
        {
            "name": "attributes",
            "type": "jsonb",
            "comment": "渠道可配置的画像属性定义",
            "required": True,
            "source": "渠道配置",
            "sensitivity": "内部",
        },
        {
            "name": "consolidation",
            "type": "jsonb",
            "comment": "后台归档与画像整理策略",
            "required": True,
            "source": "渠道配置",
            "sensitivity": "内部",
        },
    ],
    "memories": [
        {
            "name": "source_mode",
            "type": "varchar(16)",
            "comment": "来源有效性模式：独立依据或全部依赖",
            "required": True,
            "source": "记忆服务",
            "sensitivity": "内部",
        },
    ],
}


def column(value: dict[str, Any]) -> Column[Any]:
    return Column(
        value["name"],
        column_type(value["type"]),
        nullable=True,
        comment=value["comment"],
        info=value,
    )


def extend(metadata: MetaData) -> None:
    for name, values in COLUMNS.items():
        for value in values:
            metadata.tables[name].append_column(column(value))
    for definition in BASELINE:
        table = Table(
            definition["name"],
            metadata,
            *(column(v) for v in definition["columns"]),
            comment=definition["comment"],
            info=definition,
        )
        for number, names in enumerate(definition["indexes"]):
            Index(f"ix_{table.name}_{number}", *(table.c[name] for name in names))
