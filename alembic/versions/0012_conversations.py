"""建立会话、消息、轮次、摘要、上下文及共享删除任务。"""

from sqlalchemy import Column

from alembic import op
from creativity_service.modules.conversations.tables import build_metadata

revision = "0012_conversations"
down_revision = "0019_parallel_runs"
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
