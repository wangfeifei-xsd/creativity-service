"""显式登记通知与失败告警的调用服务范围，既有配置保持本人运行。"""

from sqlalchemy import Column, text
from sqlalchemy.dialects.postgresql import JSONB

from alembic import op

revision = "0032_run_subscriptions"
down_revision = "0031_identity_operations"
branch_labels = None
depends_on = None


def upgrade() -> None:
    for name in ("webhook_endpoints", "alert_rules"):
        op.add_column(
            name,
            Column(
                "client_ids",
                JSONB(),
                nullable=True,
                comment="订阅的调用服务列表；空列表仅包含配置者运行",
            ),
        )
        # 显式迁移已有配置，不设置数据库默认值，也不扩大原有订阅范围。
        op.execute(text(f"UPDATE {name} SET client_ids = '[]'::jsonb"))


def downgrade() -> None:
    for name in ("alert_rules", "webhook_endpoints"):
        op.drop_column(name, "client_ids")
