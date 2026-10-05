"""新增菜单目录、角色菜单选择与管理列表普通索引。"""

import json
from datetime import UTC, datetime
from pathlib import Path

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "0035_management"
down_revision = "0034_admission_indexes"
branch_labels = None
depends_on = None


def upgrade() -> None:
    table = op.create_table(
        "iam_menus",
        sa.Column("id", sa.String(64), nullable=True, comment="记录标识"),
        sa.Column("channel_id", sa.String(64), nullable=True, comment="所属渠道标识"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=True, comment="创建时间"),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True, comment="更新时间"),
        sa.Column("revision", sa.BigInteger(), nullable=True, comment="并发修订号"),
        sa.Column("name", sa.String(128), nullable=True, comment="菜单名称"),
        sa.Column("kind", sa.String(16), nullable=True, comment="节点类型"),
        sa.Column("parent_id", sa.String(64), nullable=True, comment="父节点标识"),
        sa.Column("page_key", sa.String(64), nullable=True, comment="已注册页面标识"),
        sa.Column("action_key", sa.String(64), nullable=True, comment="按钮动作标识"),
        sa.Column("workspace", sa.String(16), nullable=True, comment="适用工作区"),
        sa.Column("sort_order", sa.Integer(), nullable=True, comment="显示顺序"),
        sa.Column("visible", sa.Boolean(), nullable=True, comment="菜单可见标记"),
        sa.Column("active", sa.Boolean(), nullable=True, comment="启用标记"),
        comment="平台菜单目录",
    )
    op.create_index("ix_iam_menus_0", "iam_menus", ["channel_id", "id"], unique=False)
    op.create_index(
        "ix_iam_menus_1", "iam_menus", ["channel_id", "parent_id", "sort_order"], unique=False
    )
    op.add_column(
        "custom_roles",
        sa.Column(
            "menu_ids",
            postgresql.JSONB(),
            nullable=True,
            comment="可见菜单节点清单，空值沿用按动作生成",
        ),
    )
    op.create_table_comment(
        "custom_roles", "平台与渠道自定义角色", existing_comment="渠道自定义角色"
    )
    op.create_index(
        "ix_platform_accounts_directory",
        "platform_accounts",
        ["channel_id", "status", "login_name", "id"],
        unique=False,
    )
    op.create_index(
        "ix_audit_events_directory",
        "audit_events",
        ["channel_id", "created_at", "id"],
        unique=False,
    )
    seed = (
        Path(__file__).resolve().parents[2]
        / "src/creativity_service/modules/iam/menu_seed_v0035.json"
    )
    now = datetime.now(UTC)
    op.bulk_insert(
        table,
        [{**row, "created_at": now, "updated_at": now} for row in json.loads(seed.read_text())],
    )


def downgrade() -> None:
    op.drop_index("ix_audit_events_directory", table_name="audit_events")
    op.drop_index("ix_platform_accounts_directory", table_name="platform_accounts")
    op.drop_column("custom_roles", "menu_ids")
    op.create_table_comment(
        "custom_roles", "渠道自定义角色", existing_comment="平台与渠道自定义角色"
    )
    op.drop_table("iam_menus")
