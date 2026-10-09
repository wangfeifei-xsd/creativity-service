"""生成 MySQL 完整建表语句，字段、中文注释和普通索引在同一语句中归档。"""

from sqlalchemy import Table
from sqlalchemy.dialects import mysql
from sqlalchemy.schema import CreateIndex, CreateTable


def create_table_sql(table: Table) -> str:
    dialect = mysql.dialect(paramstyle="named")
    dialect._backslash_escapes = False
    statement = str(CreateTable(table).compile(dialect=dialect)).strip()
    indexes = []
    for index in sorted(table.indexes, key=lambda item: item.name or ""):
        compiled = str(CreateIndex(index).compile(dialect=dialect))
        name, columns = compiled.removeprefix("CREATE INDEX ").split(" ON ", 1)
        indexes.append(f"\tINDEX {name} {columns[columns.index('(') :]}")
    if indexes:
        statement = statement.replace("\n)", ",\n" + ",\n".join(indexes) + "\n)", 1)
    return "\n".join(line.rstrip() for line in statement.splitlines())
