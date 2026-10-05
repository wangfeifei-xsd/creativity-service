"""保存内置角色的菜单关联，旧角色空值继续按有效动作生成导航。"""

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

from alembic import op

revision = "0038_builtin_role_menus"
down_revision = "0037_remove_business_type"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 仅增加存储字段，不覆盖当前环境的角色配置、账号关联或密码。
    op.add_column(
        "builtin_roles",
        sa.Column(
            "menu_ids",
            JSONB(),
            nullable=True,
            comment="可见菜单节点清单，空值沿用按动作生成",
        ),
    )


def downgrade() -> None:
    op.drop_column("builtin_roles", "menu_ids")
