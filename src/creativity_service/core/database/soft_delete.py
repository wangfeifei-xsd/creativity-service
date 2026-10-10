"""统一逻辑删除字段与查询条件；清理和历史读取必须显式选择包含已删除记录。"""

from typing import Any, cast

from sqlalchemy import Boolean, Column, Select, Table, Update, and_, update
from sqlalchemy.sql import visitors
from sqlalchemy.sql.elements import ColumnElement
from sqlalchemy.sql.selectable import Alias, FromClause, Join

from creativity_service.core.primitives import utcnow

FIELD: dict[str, Any] = {
    "name": "is_deleted",
    "type": "boolean",
    "comment": "是否已逻辑删除",
    "required": True,
    "source": "服务层维护，新增为否，逻辑删除为是",
    "sensitivity": "内部",
}


def deletion_column() -> Column[bool]:
    return Column("is_deleted", Boolean(), nullable=True, comment=FIELD["comment"], info=FIELD)


def soft_delete_table(*args: Any, **kwargs: Any) -> Table:
    """在当前模型追加公共字段，不修改历史迁移使用的冻结定义。"""
    table = Table(*args, **kwargs)
    if "is_deleted" not in table.c:
        table.append_column(deletion_column())
    return table


def soft_delete(table: Table) -> Update:
    """调用方先取得业务锁；删除保留记录并推进修订，不重复删除已删除记录。"""
    return (
        update(table)
        .where(table.c.is_deleted.is_(False))
        .values(is_deleted=True, updated_at=utcnow(), revision=table.c.revision + 1)
    )


def active_rows(statement: Select[Any], *, include_deleted: bool = False) -> Select[Any]:
    """构建普通 SQLAlchemy 查询，过滤发生在计数、分页、聚合和窗口计算之前。

    外连接右侧的条件放入 ON，保留没有有效关联的左侧记录。子查询由其构建入口
    负责过滤；此处不重写子查询，允许历史查询显式保留已删除记录。
    """
    if include_deleted:
        return statement
    replacements: dict[FromClause, FromClause] = {}

    def scope(source: FromClause) -> tuple[FromClause, list[ColumnElement[bool]]]:
        if isinstance(source, Join):
            left, left_conditions = scope(source.left)
            right, right_conditions = scope(source.right)
            conditions = left_conditions
            onclause = source.onclause
            assert onclause is not None
            if source.isouter:
                onclause = and_(onclause, *right_conditions)
            else:
                conditions = [*conditions, *right_conditions]
            joined = (
                source
                if left is source.left and right is source.right and onclause is source.onclause
                else Join(left, right, onclause, isouter=source.isouter, full=source.full)
            )
            return joined, conditions
        if isinstance(source, Table) or (
            isinstance(source, Alias) and isinstance(source.element, Table)
        ):
            if "is_deleted" in source.c:
                return source, [source.c.is_deleted.is_(False)]
        return source, []

    predicates = []
    for source in statement.get_final_froms():
        replacement, conditions = scope(source)
        predicates.extend(conditions)
        if replacement is not source:
            replacements[source] = replacement
    if replacements:

        def replace(
            element: visitors.ExternallyTraversible, **kw: Any
        ) -> visitors.ExternallyTraversible | None:
            # 仅替换当前 SELECT 的外连接；保留别名、JSON_TABLE 和相关列的对象身份。
            if element is statement:
                return None
            return (
                replacements.get(element, element) if isinstance(element, FromClause) else element
            )

        statement = cast(Select[Any], visitors.replacement_traverse(statement, {}, replace))
    return statement.where(*predicates)
