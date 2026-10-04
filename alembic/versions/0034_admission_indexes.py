"""为当前配额和有效占用增加普通索引，不增加数据库业务约束。"""

from alembic import op
from creativity_service.modules.usage.indexes_v0034 import INDEXES

revision = "0034_admission_indexes"
down_revision = "0033_layered_memory"
branch_labels = None
depends_on = None


def upgrade() -> None:
    for name, table, columns in INDEXES:
        op.create_index(name, table, list(columns), unique=False)


def downgrade() -> None:
    for name, table, _ in reversed(INDEXES):
        op.drop_index(name, table_name=table)
