"""复用参数化查询结构；渠道、范围和业务值每次重新绑定，不缓存查询结果。"""

from collections.abc import Mapping
from functools import lru_cache
from typing import Any

from sqlalchemy import Select, Table, bindparam, select
from sqlalchemy.sql.elements import BindParameter, ColumnElement


@lru_cache(maxsize=512)
def _select(table: Table, shape: tuple[tuple[str, str, str], ...]) -> Select[Any]:
    predicates: list[ColumnElement[bool]] = []
    for group, name, mode in shape:
        column = table.c[name]
        if mode == "null":
            predicates.append(column.is_(None))
        else:
            parameter: BindParameter[Any] = bindparam(f"{group}_{name}", expanding=mode == "many")
            predicates.append(column.in_(parameter) if mode == "many" else column == parameter)
    return select(table).where(*predicates)


def scoped_select(
    table: Table,
    scope: Mapping[str, Any],
    filters: Mapping[str, Any],
    batches: Mapping[str, Any] | None = None,
) -> tuple[Select[Any], dict[str, Any]]:
    """范围条件与业务条件分别绑定，相同字段的业务筛选不能覆盖范围。"""
    shape = []
    parameters = {}
    for group, values in (("scope", scope), ("filter", filters), ("batch", batches or {})):
        for name in sorted(values):
            if name not in table.c:
                raise ValueError("筛选字段不存在")
            value = values[name]
            mode = "many" if group == "batch" else "null" if value is None else "eq"
            shape.append((group, name, mode))
            if mode != "null":
                parameters[f"{group}_{name}"] = value
    return _select(table, tuple(shape)), parameters


def latest_per_group(table: Table, group: str, *predicates: ColumnElement[bool]) -> Select[Any]:
    """按业务组批量读取最新记录；MySQL 使用窗口函数保持确定顺序。"""
    from sqlalchemy import func

    ranked = (
        select(
            table,
            func.row_number()
            .over(
                partition_by=table.c[group], order_by=(table.c.created_at.desc(), table.c.id.desc())
            )
            .label("row_position"),
        )
        .where(*predicates)
        .subquery()
    )
    return select(*(ranked.c[column.name] for column in table.c)).where(ranked.c.row_position == 1)
