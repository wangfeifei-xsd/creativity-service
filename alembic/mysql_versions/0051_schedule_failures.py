"""定时计划连续失败保护；升级前的历史结果不计入新一轮统计。"""

import json
from datetime import UTC, datetime

import sqlalchemy as sa
from sqlalchemy.dialects.mysql import JSON

from alembic import context, op

revision = "0051_schedule_failures"
down_revision = "0050_qdrant"
branch_labels = None
depends_on = None


def upgrade() -> None:
    inspector = None if context.is_offline_mode() else sa.inspect(op.get_bind())
    existing = (
        set()
        if inspector is None
        else {c["name"] for c in inspector.get_columns("automation_schedules")}
    )
    for column in (
        sa.Column(
            "consecutive_failures", sa.Integer(), nullable=True, comment="定时计划连续失败次数"
        ),
        sa.Column("outcome_cursor", JSON(), nullable=True, comment="按触发顺序已统计的条目游标"),
    ):
        if column.name not in existing:
            op.add_column("automation_schedules", column)
    table = sa.table(
        "automation_schedules",
        sa.column("consecutive_failures", sa.Integer()),
        sa.column("outcome_cursor", JSON()),
    )
    op.execute(
        table.update().where(table.c.consecutive_failures.is_(None)).values(consecutive_failures=0)
    )
    # 游标与计数均由应用显式初始化；不设置数据库默认值或业务触发器。
    cursor = json.dumps({"created_at": datetime.now(UTC).isoformat(), "id": ""})
    op.execute(
        table.update()
        .where(table.c.outcome_cursor.is_(None))
        .values(outcome_cursor=op.inline_literal(cursor))
    )
    indexes = (
        set()
        if inspector is None
        else {i["name"] for i in inspector.get_indexes("automation_items")}
    )
    if "ix_automation_items_schedule_outcome" not in indexes:
        op.create_index(
            "ix_automation_items_schedule_outcome",
            "automation_items",
            ["channel_id", "schedule_id", "created_at", "id"],
            unique=False,
        )


def downgrade() -> None:
    op.drop_index("ix_automation_items_schedule_outcome", table_name="automation_items")
    op.drop_column("automation_schedules", "outcome_cursor")
    op.drop_column("automation_schedules", "consecutive_failures")
