"""统一持久化角色目录与账号角色选择，不改变历史渠道授权。"""

import json
from datetime import UTC, datetime
from pathlib import Path

import sqlalchemy as sa

from alembic import op

revision = "0036_role_catalog"
down_revision = "0035_management"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "builtin_roles",
        sa.Column("account_assignable", sa.Boolean(), nullable=True, comment="可用于账号管理"),
    )
    op.add_column(
        "platform_accounts",
        sa.Column("role_id", sa.String(64), nullable=True, comment="账号选择的管理角色"),
    )
    op.add_column(
        "custom_roles",
        sa.Column("grant_scope", sa.String(32), nullable=True, comment="授权类别"),
    )
    connection = op.get_bind()
    custom = sa.table("custom_roles", sa.column("channel_id"), sa.column("grant_scope"))
    connection.execute(
        custom.update().values(
            grant_scope=sa.case((custom.c.channel_id == "system", "platform"), else_="channel")
        )
    )
    table = sa.table(
        "builtin_roles",
        sa.column("id", sa.String(64)),
        sa.column("channel_id", sa.String(64)),
        sa.column("revision", sa.BigInteger()),
        sa.column("created_at", sa.DateTime(timezone=True)),
        sa.column("updated_at", sa.DateTime(timezone=True)),
        sa.column("role_code", sa.String(64)),
        sa.column("name", sa.String(128)),
        sa.column("allowed_actions", sa.JSON()),
        sa.column("grant_scope", sa.String(32)),
        sa.column("account_assignable", sa.Boolean()),
    )
    seed = (
        Path(__file__).resolve().parents[2]
        / "src/creativity_service/modules/iam/role_seed_v0036.json"
    )
    existing = set(
        connection.scalars(sa.select(table.c.role_code).where(table.c.channel_id == "system"))
    )
    connection.execute(table.update().values(account_assignable=False))
    now = datetime.now(UTC)
    # 开发库可能已初始化角色，只补缺失项，不覆盖现有记录。
    for row in json.loads(seed.read_text()):
        if row["role_code"] not in existing:
            connection.execute(table.insert().values(**row, created_at=now, updated_at=now))
        else:
            connection.execute(
                table.update()
                .where(table.c.channel_id == "system", table.c.role_code == row["role_code"])
                .values(account_assignable=row["account_assignable"])
            )


def downgrade() -> None:
    op.drop_column("builtin_roles", "account_assignable")
    op.drop_column("custom_roles", "grant_scope")
    op.drop_column("platform_accounts", "role_id")
