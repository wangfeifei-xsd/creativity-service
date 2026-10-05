"""删除渠道的冗余业务分类，不改变渠道身份、映射及授权记录。"""

import sqlalchemy as sa

from alembic import op

revision = "0037_remove_business_type"
down_revision = "0036_role_catalog"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_column("channels", "business_type")


def downgrade() -> None:
    # 回退只恢复可空列，已清除的分类值不能重建。
    op.add_column(
        "channels",
        sa.Column(
            "business_type",
            sa.String(32),
            nullable=True,
            comment="可选业务分类展示文本，历史分类原值保留",
        ),
    )
