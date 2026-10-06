"""供应商连接维护出站网络范围，旧连接沿用仅公网策略。"""

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

from alembic import op

revision = "0040_model_networks"
down_revision = "0039_account_roles"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "model_connections",
        sa.Column(
            "allowed_networks",
            JSONB(),
            nullable=True,
            comment="连接允许的 IP 网段，空数组仅允许公网",
        ),
    )
    op.execute("UPDATE model_connections SET allowed_networks = '[]'::jsonb")


def downgrade() -> None:
    op.drop_column("model_connections", "allowed_networks")
