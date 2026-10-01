"""公共模型的冻结初始定义；迁移按版本引用，不自动建表。"""

import json
import re
from pathlib import Path
from typing import Any

from sqlalchemy import (
    BigInteger,
    Boolean,
    Column,
    DateTime,
    Index,
    Integer,
    LargeBinary,
    MetaData,
    Numeric,
    String,
    Table,
    Text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.types import TypeEngine

BASELINE = json.loads(Path(__file__).with_name("baseline_v0001.json").read_text(encoding="utf-8"))


def column_type(name: str) -> TypeEngine[Any]:
    if match := re.fullmatch(r"varchar\((\d+)\)", name):
        return String(int(match[1]))
    if match := re.fullmatch(r"numeric\((\d+),(\d+)\)", name):
        return Numeric(int(match[1]), int(match[2]))
    return {
        "text": Text(),
        "jsonb": JSONB(),
        "bigint": BigInteger(),
        "integer": Integer(),
        "timestamptz": DateTime(timezone=True),
        "boolean": Boolean(),
        "bytea": LargeBinary(),
    }[name]


def build_metadata() -> MetaData:
    metadata = MetaData()
    for definition in BASELINE:
        table = Table(
            definition["name"],
            metadata,
            *(
                Column(
                    field["name"],
                    column_type(field["type"]),
                    nullable=True,
                    comment=field["comment"],
                    info=field,
                )
                for field in definition["columns"]
            ),
            comment=definition["comment"],
            info=definition,
        )
        for number, columns in enumerate(definition["indexes"]):
            Index(f"ix_{table.name}_{number}", *(table.c[name] for name in columns))
    return metadata


metadata = build_metadata()
