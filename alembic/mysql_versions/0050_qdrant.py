"""向量后端切换 Qdrant，同步任务表保持后端无关的中文说明。"""

from alembic import op

revision = "0050_qdrant"
down_revision = "0049_soft_delete"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table_comment("memory_index_tasks", "向量同步与删除持久化任务")


def downgrade() -> None:
    op.create_table_comment("memory_index_tasks", "Milvus 向量同步与删除持久化任务")
