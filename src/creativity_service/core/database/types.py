"""MySQL 的时间与 JSON 语义，时间入库统一 UTC、出库恢复时区。"""

import json
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import JSON, String, column, func, literal, literal_column
from sqlalchemy.dialects.mysql import DATETIME
from sqlalchemy.engine import Dialect
from sqlalchemy.types import TypeDecorator


class UTCDateTime(TypeDecorator[datetime]):
    impl = DATETIME(fsp=6)
    cache_ok = True

    @property
    def python_type(self) -> type[datetime]:
        return datetime

    def process_bind_param(self, value: datetime | None, dialect: Dialect) -> datetime | None:
        if value is None:
            return None
        if value.tzinfo is None:
            raise ValueError("数据库时间必须包含时区")
        return value.astimezone(UTC).replace(tzinfo=None)

    def process_result_value(self, value: datetime | None, dialect: Dialect) -> datetime | None:
        return value.replace(tzinfo=UTC) if value is not None else None


class DocumentJSON(JSON):
    """JSON 包含判断使用结构匹配，不能退化为字符串 LIKE。"""

    class Comparator(JSON.Comparator[Any]):
        def contains(self, other: Any, **kwargs: Any) -> Any:
            candidate = literal(json.dumps(other, ensure_ascii=False))
            return func.json_contains(self.expr, candidate) == 1

        def has_key(self, key: str) -> Any:
            """按完整对象键判断存在性，键中的点号和引号不作为路径语法。"""
            path = literal("$." + json.dumps(key, ensure_ascii=False))
            return func.json_contains_path(self.expr, "one", path) == 1

    comparator_factory = Comparator


def json_array_rows(expression: Any) -> Any:
    """在数据库内展开 JSON 数组，供批量关联与计数使用。"""
    return func.json_table(
        expression,
        literal_column("'$[*]' COLUMNS (value VARCHAR(255) PATH '$')"),
    ).table_valued(column("value", String(255)))
