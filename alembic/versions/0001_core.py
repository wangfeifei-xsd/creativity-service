"""建立公共版本、审计、凭据与内容生命周期基础表。"""

from sqlalchemy import Column

from alembic import op
from creativity_service.core.database.tables import build_metadata

revision = "0001_core"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    for table in build_metadata().tables.values():
        op.create_table(
            table.name,
            *(Column(c.name, c.type, nullable=True, comment=c.comment) for c in table.c),
            comment=table.comment,
        )
        for index in sorted(table.indexes, key=lambda value: value.name):
            op.create_index(index.name, table.name, [c.name for c in index.columns], unique=False)


def downgrade() -> None:
    for table in reversed(list(build_metadata().tables.values())):
        for index in sorted(table.indexes, key=lambda value: value.name):
            op.drop_index(index.name, table_name=table.name)
        op.drop_table(table.name)
