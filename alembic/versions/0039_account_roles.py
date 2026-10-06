"""账号管理支持多角色选择，旧账号继续按已有角色与成员授权解析。"""

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

from alembic import op

revision = "0039_account_roles"
down_revision = "0038_builtin_role_menus"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "platform_accounts",
        sa.Column(
            "role_ids",
            JSONB(),
            nullable=True,
            comment="账号选择的管理角色清单，空值兼容旧单角色",
        ),
    )


def downgrade() -> None:
    op.drop_column("platform_accounts", "role_ids")
